#!/usr/bin/env python3
"""Build and validate an op25 multi_rx.py config for LWIN Baton Rouge site 13.

Why a generator rather than a checked-in JSON file: every number here fails
SILENTLY when it is wrong.

  * A frequency outside its device's window makes change_freq return False.
    op25's tk_p25.py used to claim the talkgroup anyway and record silence for
    the whole call (fixed by patches/op25-tk_p25-release-unreachable-grant.patch,
    but a config that needs the fix is still a config that loses coverage).
  * An if_rate that does not match get_decim's second stage costs an
    arb_resampler per channel and never says so. With two devices at different
    rates it is easy to give a channel the other device's if_rate.
  * A device centre landing on a real channel puts the DC spike in its
    passband. That is the trap of commit cf019d4.
  * UDP ports closer than 2 apart collide: op25_audio.cc:298 sends on
    d_audio_port + slot_id and udp_audio_record.py binds PORT and PORT+1.
  * Two devices sharing a serial silently opens one radio twice.

validate() asserts all of it before a radio is opened.

Usage:
    python3 scripts/make_multirx_cfg.py --legs 700,800 \\
        --whitelist "$PWD/lwin_active_whitelist.txt" \\
        --cc-whitelist "$PWD/lwin_nofollow.txt" \\
        -o lwin_both.json
"""
from __future__ import annotations

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# Copied from op25's p25_demodulator_dev.get_decim -- the module multi_rx.py:62
# actually imports. NOT p25_demodulator.py, whose set_relative_frequency bound
# differs (if1/2 rather than if_rate/2). Duplicated rather than imported because
# op25 lives outside this package and importing it drags in GNU Radio.
# ---------------------------------------------------------------------------
def get_decim(speed: int) -> tuple[int, int] | None:
    s = int(speed)
    for i_f in (24000, 25000, 32000):
        if s % i_f != 0:
            continue
        q = s // i_f
        if q & 1:
            continue
        if q >= 40 and q & 3 == 0:
            return q // 4, 4
        return q // 2, 2
    return None


def if_rate_for(rate: int) -> int:
    """The if2 get_decim lands on -- the only if_rate that avoids a resampler."""
    d = get_decim(rate)
    if d is None:
        raise ValueError(
            f'op25 cannot two-stage decimate {rate} Hz; pick a rate divisible '
            f'by 24000, 25000 or 32000 with an even quotient')
    decim, decim2 = d
    return rate // decim // decim2


def usable_half_span(rate: int, usable_bw: float, if_rate: int) -> float:
    """p25_demodulator_dev.set_relative_frequency's bound, in Hz.

        abs(offset) > (input_rate * usable_bw)/2 - if_rate/2  ->  refuse to tune

    if_rate (24-25 kHz), not if1 (96-100 kHz).
    """
    return (rate * usable_bw) / 2 - if_rate / 2


# ---------------------------------------------------------------------------
# The radios. Addressed by SERIAL, never by index: on 2026-08-31 a second
# HackRF was plugged in and enumerated as index 0 ahead of the Pro, silently
# repointing every script. `soapy=0` stays for BOTH -- it is gr-osmosdr's own
# source index and the serial makes the match unique. Verified: both open
# concurrently with soapy=0 plus distinct serials, at 12 and 8 Msps.
#
# Gains are PER RADIO and not interchangeable. The One's input runs 13.7 dB
# hotter: VGA:44 clips 0.64% of samples on it, VGA:14 drops the control channel
# to 12.7 dB (below op25's threshold), VGA:20 gives 21.4 dB at 0.0000% clipping.
# Mirrors scripts/radios.sh. See docs/2026-08-31-wideband-multichannel.md 10.3.
# ---------------------------------------------------------------------------
RADIOS = {
    'one': {
        'serial': '0000000000000000930c64dc275e54c3',   # HackRF One r10
        'gains': 'AMP:0,LNA:40,VGA:20',
    },
    'pro': {
        'serial': '0000000000000000977c64de2d717413',   # HackRF Pro r1.2
        'gains': 'AMP:0,LNA:40,VGA:44',
    },
}

# ---------------------------------------------------------------------------
# The two legs of LWIN RFSS 1 site 13 "Baton Rouge Simulcast", 87 MHz apart.
# Voice frequencies: the grants table in sdr.db (3,765 grants over 359 s, a
# complete census -- the receiver never left the control channel), plus the
# site's other RadioReference channels that fall inside the same window.
# ---------------------------------------------------------------------------
LEG_700 = {
    'name': '700',
    'radio': 'one',
    'centre': 771_418_500,        # 662 kHz clear of the nearest audible carrier
    'rate': 8_000_000,
    'n_voice': 3,                 # 3 receivers took 28/28 of this leg's calls
    'voice': [769_681_250, 769_931_250, 770_756_250, 772_681_250],
    # 773.05625 is the ACTIVE control channel: 1,459 TSBK updates / 26 talkgroups
    # / 48 radio IDs / 1 startup timeout in 75 s on the One at VGA:20.
    # 774.54375 is a live alternate and is inside the window (+3.125 MHz).
    'control': [773_056_250, 774_543_750],
    'dc_guard': 100_000,
}

LEG_800 = {
    'name': '800',
    'radio': 'pro',
    'centre': 855_725_000,
    'rate': 12_000_000,
    'n_voice': 5,                 # 5 receivers took 81/83 of this leg's calls
    'voice': [851_287_500, 851_837_500, 852_037_500, 852_150_000, 852_350_000,
              852_562_500, 852_750_000, 852_912_500, 852_987_500,
              855_987_500, 856_237_500, 856_462_500,
              857_237_500, 858_237_500, 859_237_500, 860_237_500],
    # RadioReference lists 851.0375 and 851.4875 as control channels for this
    # site. Both MEASURED DEAD on 2026-08-31: +0.5 dB and +0.4 dB, 0%
    # continuity, over 10 s at 12 Msps with the good antenna. Deliberately NOT
    # listed: op25's next_cc rotation would stall on a dead channel for seconds
    # at a time. They remain inside the window (offset 4.6875 MHz < 5.088) so a
    # real failover would still be reachable if they ever come up.
    'control': [],
    'dc_guard': 100_000,
}

LEGS = {'700': LEG_700, '800': LEG_800}


def widest_offset(leg: dict) -> float:
    """Largest |channel - centre| this leg must reach, in Hz."""
    return max(abs(f - leg['centre']) for f in leg['voice'] + leg['control'])


def build(legs: list[dict], *, whitelist: str, cc_whitelist: str,
          tgid_tags: str = '', base_port: int = 23460, nac: str = '0x1bd',
          sysname: str = 'LWIN-BR', usable_bw: float = 0.85,
          crypt_behavior: int = 1) -> dict:
    """One channel pinned to the control channel, plus n_voice per leg."""
    if not legs:
        raise ValueError('need at least one leg')
    for leg in legs:
        if leg['n_voice'] < 1:
            raise ValueError(f"leg {leg['name']} needs at least one voice channel")
    control = [f for leg in legs for f in leg['control']]
    if not control:
        raise ValueError(
            'no leg carries a live control channel, so nothing would hold it '
            'and no grant would ever be seen. The 800 leg cannot stand alone: '
            "site 13's control channel is 773.05625, in the 700 leg.")

    devices, channels, port = [], [], base_port

    def chan(name, radio, freq, wl, port, if_rate):
        return {
            'name': name,
            'device': radio,
            'trunking_sysname': sysname,
            'demod_type': 'cqpsk',
            'filter_type': 'rc',
            'excess_bw': 0.2,
            'frequency': freq,
            'if_rate': if_rate,
            'symbol_rate': 4800,        # P25 Phase 1 C4FM; this system is Phase I
            'destination': f'udp://127.0.0.1:{port}',
            'meta_stream_name': '',
            'plot': '',
            'enable_analog': 'off',
            'whitelist': wl,
            'blacklist': '',
            'crypt_keys': '',
            'crypt_behavior': crypt_behavior,
        }

    for leg in legs:
        radio = leg['radio']
        if_rate = if_rate_for(leg['rate'])
        devices.append({
            'name': radio,
            'args': f"soapy=0,driver=hackrf,serial={RADIOS[radio]['serial']}",
            'gains': RADIOS[radio]['gains'],
            'frequency': leg['centre'],
            'rate': leg['rate'],
            'usable_bw_pct': usable_bw,
            'tunable': False,   # mandatory: multi_rx.py:754 will not share a tunable device
            'offset': 0,
            'ppm': 0.0,
        })
        # The control receiver goes on whichever leg actually has one. Its
        # whitelist holds only a talkgroup that does not exist, so
        # find_talkgroup never matches and it never calls tune_voice -- the
        # trick lwin_cdr_run.sh uses. It keeps the grant census at 100% while
        # the other receivers record audio, which OBSERVATIONS.md 3.3 records
        # as impossible with a single receiver.
        if leg['control']:
            channels.append(chan('CC', radio, leg['control'][0],
                                 cc_whitelist, port, if_rate))
            port += 2
        for i in range(leg['n_voice']):
            start = leg['voice'][i % len(leg['voice'])]
            channels.append(chan(f"VC{leg['name']}_{i}", radio, start,
                                 whitelist, port, if_rate))
            port += 2

    return {
        'channels': channels,
        'devices': devices,
        'trunking': {
            'module': 'tk_p25.py',
            'chans': [{
                'nac': nac,
                'sysname': sysname,
                'control_channel_list': ','.join(
                    f'{f/1e6:.5f}'.rstrip('0') for f in control),
                'whitelist': '',
                'blacklist': '',
                'tgid_tags_file': tgid_tags,
                'rid_tags_file': '',
                'tdma_cc': False,
                'crypt_behavior': crypt_behavior,
            }],
        },
        # No "audio" section: this host has no sound card and snd-aloop is
        # unavailable, so op25's -U/-O paths are unusable (OBSERVATIONS 3.4).
        # Audio leaves over UDP only. No "terminal" section either: multi_rx
        # treats both as optional (multi_rx.py:578-597), and omitting the
        # terminal stops op25 rewriting a curses status line into the log we
        # tail for per-call metadata.
    }


def validate(cfg: dict, legs: list[dict]) -> None:
    by_name = {d['name']: d for d in cfg['devices']}
    leg_by_radio = {leg['radio']: leg for leg in legs}

    serials = [d['args'] for d in cfg['devices']]
    if len(set(serials)) != len(serials):
        raise ValueError('two devices share the same serial in their args; '
                         'that opens one radio twice')

    chans_per_dev: dict[str, int] = {}
    for ch in cfg['channels']:
        chans_per_dev[ch['device']] = chans_per_dev.get(ch['device'], 0) + 1

    for name, dev in by_name.items():
        if dev['tunable'] and chans_per_dev.get(name, 0) > 1:
            raise ValueError(
                f"device {name} is marked tunable and carries "
                f"{chans_per_dev[name]} channels; multi_rx.py:754 will drop "
                f"every channel after the first")

    for ch in cfg['channels']:
        dev = by_name[ch['device']]
        want = if_rate_for(dev['rate'])
        if ch['if_rate'] != want:
            raise ValueError(
                f"channel {ch['name']} has if_rate {ch['if_rate']} but its "
                f"device {dev['name']} runs {dev['rate']} Hz, where get_decim "
                f"lands on {want}; every such channel pays an arb_resampler")

    for name, dev in by_name.items():
        leg = leg_by_radio[name]
        limit = usable_half_span(dev['rate'], dev['usable_bw_pct'],
                                 if_rate_for(dev['rate']))
        for f in leg['voice'] + leg['control']:
            off = abs(f - dev['frequency'])
            if off > limit:
                raise ValueError(
                    f"{f/1e6:.5f} MHz is outside device {name}'s window: "
                    f"offset {off/1e6:.4f} MHz > limit {limit/1e6:.4f} MHz")
            if off < leg['dc_guard']:
                raise ValueError(
                    f"device {name}'s centre {dev['frequency']/1e6:.5f} is "
                    f"within {leg['dc_guard']/1e3:.0f} kHz of channel "
                    f"{f/1e6:.5f}; the DC spike would land in its passband "
                    f"(see commit cf019d4)")

    ports = sorted(int(ch['destination'].rsplit(':', 1)[1])
                   for ch in cfg['channels'])
    for a, b in zip(ports, ports[1:]):
        if b - a < 2:
            raise ValueError(
                f'UDP ports {a} and {b} are less than 2 apart; op25 sends on '
                f'port+slot_id and the recorder binds port and port+1')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--legs', default='700',
                    help='comma-separated: 700, 800, or 700,800')
    ap.add_argument('--whitelist', required=True)
    ap.add_argument('--cc-whitelist', required=True,
                    help='a file holding only a non-existent talkgroup, so the '
                         'control receiver never retunes')
    ap.add_argument('--tgid-tags', default='')
    ap.add_argument('--base-port', type=int, default=23460)
    ap.add_argument('--n-voice-700', type=int)
    ap.add_argument('--n-voice-800', type=int)
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()

    legs = []
    for key in [k.strip() for k in a.legs.split(',') if k.strip()]:
        if key not in LEGS:
            ap.error(f'unknown leg {key!r}; choose from {sorted(LEGS)}')
        leg = dict(LEGS[key])
        override = getattr(a, f'n_voice_{key}')
        if override is not None:
            leg['n_voice'] = override
        legs.append(leg)

    cfg = build(legs, whitelist=a.whitelist, cc_whitelist=a.cc_whitelist,
                tgid_tags=a.tgid_tags, base_port=a.base_port)
    validate(cfg, legs)

    with open(a.out, 'w') as fh:
        json.dump(cfg, fh, indent=4)
        fh.write('\n')

    print(f"{a.out}: {len(cfg['devices'])} device(s), "
          f"{len(cfg['channels'])} channel(s)")
    for leg in legs:
        dev = next(d for d in cfg['devices'] if d['name'] == leg['radio'])
        limit = usable_half_span(dev['rate'], dev['usable_bw_pct'],
                                 if_rate_for(dev['rate']))
        need = widest_offset(leg)
        ports = [int(c['destination'].rsplit(':', 1)[1])
                 for c in cfg['channels'] if c['device'] == leg['radio']]
        print(f"  leg {leg['name']:>3} on {leg['radio']:>3} "
              f"({RADIOS[leg['radio']]['serial'][-16:]}): "
              f"{leg['rate']/1e6:.0f} Msps @ {leg['centre']/1e6:.4f} MHz, "
              f"if_rate {if_rate_for(leg['rate'])}")
        print(f"        window +/-{limit/1e6:.4f} MHz, widest offset "
              f"+/-{need/1e6:.4f} MHz, margin {(limit-need)/1e6:.4f} MHz")
        print(f"        gains {RADIOS[leg['radio']]['gains']}, "
              f"{leg['n_voice']} voice"
              f"{' + 1 pinned control' if leg['control'] else ''}, "
              f"ports {min(ports)}-{max(ports)}")
    print(f"  control channel list: "
          f"{cfg['trunking']['chans'][0]['control_channel_list']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
