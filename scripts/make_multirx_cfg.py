#!/usr/bin/env python3
"""Build and validate an op25 multi_rx.py config for LWIN Baton Rouge site 13.

Why a generator rather than a checked-in JSON file: every number here fails
SILENTLY when it is wrong.

  * A frequency outside its device's window makes change_freq return False.
    op25's tk_p25.py used to claim the talkgroup anyway and record silence for
    the whole call (fixed by patches/op25-tk_p25-release-unreachable-grant.patch,
    but a config that needs the fix is still a config that loses coverage).
  * An if_rate that does not divide its device's sample rate exactly costs an
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
import os
import sys

# ---------------------------------------------------------------------------
# multi_rx.py:62 imports p25_demodulator_dev, and its p25_demod_cb builds a
# SINGLE-STAGE freq_xlating chain:
#
#     decimation     = int(input_rate / if_rate)
#     resampled_rate = input_rate / decimation
#     if self.if_rate != resampled_rate: <insert arb_resampler>
#
# So the rule that avoids a per-channel resampler is simply that **if_rate must
# divide input_rate exactly**. p25_demodulator_dev does NOT call get_decim --
# that is the two-stage chain in the non-_dev p25_demodulator.py, which
# multi_rx never imports. get_decim is kept below only as a cross-check,
# because it always returns an exact divisor and is therefore always safe, just
# needlessly strict: it refuses odd quotients that _dev handles fine.
#
# Confirmed against a live 9-channel run (results/op25_multi.log):
#     xlator if_rate=25000, input_rate=8000000,  decim=320, resampled_rate=25000
#     xlator if_rate=24000, input_rate=12000000, decim=500, resampled_rate=24000
# Both equal, so no arb_resampler was built on either device.
#
# Duplicated rather than imported because op25 lives outside this package and
# importing it drags in GNU Radio.
# ---------------------------------------------------------------------------

# op25's own candidate order, from get_decim's if_freqs list. Order matters:
# it keeps 8 Msps on 25000 and 12 Msps on 24000, which is the pairing verified
# on the air. Preferring the largest divisor instead would silently move both
# to 32000.
IF_RATE_CANDIDATES = (24000, 25000, 32000)


def get_decim(speed: int) -> tuple[int, int] | None:
    """p25_demodulator.py's two-stage rule. NOT what multi_rx uses.

    Kept as a cross-check and as documentation of the other module: whatever it
    returns is always an exact divisor, so it is always safe, but it refuses odd
    quotients (1_025_000 = 25000 x 41) that _dev handles without a resampler.
    """
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
    """The first candidate if_rate that divides `rate` exactly.

    Exact division is the whole requirement: p25_demodulator_dev inserts an
    arb_resampler per channel when if_rate != input_rate/int(input_rate/if_rate).
    """
    for i_f in IF_RATE_CANDIDATES:
        if rate % i_f == 0:
            return i_f
    raise ValueError(
        f'no supported if_rate divides {rate} Hz exactly (tried '
        f'{", ".join(str(i) for i in IF_RATE_CANDIDATES)}); every channel would '
        f'pay an arb_resampler')


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
    # 3 receivers took 28/28 of this leg's calls in the original census, and
    # the later concurrency measurement (see LEG_800's comment below) found
    # the 700 leg has NEVER exceeded 2 concurrent calls across 1,354 calls --
    # so 3 stays as-is: it is already one spare over the observed peak, and a
    # leg that has never been censused as thoroughly as the 800 leg is worth
    # keeping that spare on rather than trimming to the bare peak of 2.
    'n_voice': 3,
    'voice': [769_681_250, 769_931_250, 770_756_250, 772_681_250],
    # 773.05625 is the ACTIVE control channel: 1,459 TSBK updates / 26 talkgroups
    # / 48 radio IDs / 1 startup timeout in 75 s on the One at VGA:20.
    # 774.54375 is a live alternate and is inside the window (+3.125 MHz).
    'control': [773_056_250, 774_543_750],
    # STARTING frequency for this leg's SNDCP data receiver. NOT "the data
    # channel" -- there isn't one.
    #
    # An early 35-minute sample saw all 362 data grants (TSBK 0x14) name
    # 0x14cc = 769.68125, which looked like a fixed assignment. It is not. Over
    # 11 hours: 8,084 grants across 19 DIFFERENT frequencies, 78% of them on the
    # 800 leg, allocated out of the ordinary traffic-channel pool exactly as
    # voice is. 769.68125 carries about 4% of them.
    #
    # So the receiver FOLLOWS grants -- tk_p25.py's tune_data_receivers moves it
    # on every 0x14 -- and this list only says where it waits beforehand. One
    # entry is one receiver.
    #
    # A NOTE ON WHAT THIS CAN AND CANNOT HEAR. iden_up id 1 carries toff +30
    # MHz, so a grant naming 769.68125 assigns the pair 769.68125 down /
    # 799.68125 up. This receiver is on the DOWNLINK, so it hears outbound data
    # (system -> radio) only. Inbound reports -- which is where LRRP positions
    # and ARS registrations travel -- are at 799-805 MHz, outside every window
    # this config can reach. Reading those needs another receiver and antenna.
    'data': [769_681_250],
    'dc_guard': 100_000,
}

LEG_800 = {
    'name': '800',
    'radio': 'pro',
    'centre': 855_725_000,
    'rate': 12_000_000,
    # Concurrency measured from `calls` (start, dur, freq) across 8,490 calls
    # with frequency recorded, split by leg at 800 MHz:
    #
    #   700 leg: 1,354 calls   peak concurrency 2 of 3 receivers   at/above capacity:  0     (0.0%)
    #            distribution: 1 -> 1273,  2 -> 81
    #   800 leg: 7,136 calls   peak concurrency 5 of 5 receivers   at/above capacity: 17     (0.2%)
    #            distribution: 1 -> 4574, 2 -> 1907, 3 -> 509, 4 -> 129, 5 -> 17
    #
    # The 800 leg carries 84% of all traffic and hit its old ceiling of 5
    # seventeen times -- each one a moment a 6th simultaneous call had nowhere
    # to go. Dropped calls are invisible in this data by definition (a call
    # that never got a receiver was never recorded), so at-capacity events are
    # the only available proxy, and it agrees with the census already cited in
    # server/api/listen/start.post.ts's MAX_VOICE comment: 5 receivers took
    # 81/83 of the 800 leg's calls, ~2.4% missed. Raised 5 -> 7, not to 8
    # (MAX_VOICE): 7 covers the measured peak (5) with two full spares, and
    # nothing in this data justifies provisioning past that. The 700 leg's
    # n_voice stays at 3 above -- it has NEVER exceeded 2 concurrent calls in
    # 1,354 calls, so 3 already gives it a spare over its observed peak.
    'n_voice': 7,
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
    # 856.4625 is announced as a data channel by the same TSBK 0x16 that names
    # 769.68125 (2,953 announcements against 2,941 over the same 35 minutes, so
    # the site advertises both equally). It gets NO receiver because it took
    # zero of the 362 observed data grants -- every one went to the 700 leg.
    # Announced and idle is not the same as carrying traffic, and the 800 leg's
    # receivers are the scarce ones: it carries 84% of voice. Populate this if
    # grants ever appear here.
    'data': [],
    'dc_guard': 100_000,
}

LEGS = {'700': LEG_700, '800': LEG_800}

# ------------------------------------------------------------------ the port block
# The UDP block this config is allowed to occupy, ASSERTED rather than merely
# described. Until now 23460 and 23492 appeared only in prose -- in
# scripts/capture_control.py's MIN_VOICE/MAX_VOICE comment and in
# server/api/listen/start.post.ts's -- and validate() bounded port SPACING
# (>= 2 apart) with no ceiling at all. Both ends of the budget were
# comment-bound, and the budget is at exactly zero headroom:
#
#     channels = 1 control + n_voice_700 + n_voice_800 + 1 SNDCP data receiver
#     last port = BASE_PORT + 2 * (channels - 1)
#
# At the MAX_VOICE of 8 that both front doors enforce, that is 18 channels and
# a last port of 23460 + 2*17 = 23494. Exact. Nothing spare.
#
# It was 17 channels ending at 23492 until the pinned SNDCP data receiver was
# added (see LEG_700['data']). That receiver is not optional capacity an
# operator dials up -- it is one channel, always, on any leg that declares a
# data frequency -- so it is counted into the budget here rather than being
# allowed to silently consume the headroom that did not exist. Widening the
# block by 2 is the change the ValueError below has always demanded of anyone
# who grew this config, and it was made in all three places at once:
# here, scripts/capture_control.py's MAX_VOICE comment, and
# server/api/listen/start.post.ts's.
#
# And there is a live path to overflow already written into this file:
# LEG_800's comment says its two dead control channels "remain inside the
# window ... so a real failover would still be reachable if they ever come
# up." Repopulating LEG_800['control'] makes 18 channels and a last port of
# 23494 -- outside the block, with nothing anywhere failing. That is what the
# span check in validate() now catches.
#
# The bound is on the SPAN rather than on the absolute value 23492 on purpose:
# --base-port is a real flag (lwin_listen_multi.sh passes $BASE_PORT through
# it), and an operator moving the whole block to a free range is legitimate.
# What is never legitimate is the block growing wider than the window that was
# sized for it.
BASE_PORT = 23460
LAST_PORT = 23494
PORT_BLOCK_SPAN = LAST_PORT - BASE_PORT          # 34 -> 18 channels, 2 apart


def leg_freqs(leg: dict) -> list[int]:
    """Every frequency this leg must be able to reach.

    One list, used by widest_offset and by both of validate()'s window checks,
    so a frequency added to a leg cannot be range-checked in one place and
    skipped in another.
    """
    return leg['voice'] + leg['control'] + leg.get('data', [])


def widest_offset(leg: dict) -> float:
    """Largest |channel - centre| this leg must reach, in Hz."""
    return max(abs(f - leg['centre']) for f in leg_freqs(leg))


def build(legs: list[dict], *, crypt_keys: str = '', whitelist: str, cc_whitelist: str,
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

    def chan(name, radio, freq, wl, port, if_rate, lo, hi, sysname=sysname,
             data_only=False):
        # Decryption keys go on VOICE channels only: the control channel carries
        # no voice, so loading them there would do nothing. Empty string means
        # "no keys", which is op25's own default and leaves encrypted bursts
        # silenced.
        keys = crypt_keys if name.startswith('VC') else ''
        return {
            'name': name,
            'device': radio,
            # Frequencies this channel's device can actually reach. op25's
            # find_talkgroup picks by priority and claim status alone and would
            # otherwise hand this receiver grants on the other band, which it
            # cannot tune -- see
            # patches/op25-tk_p25-window-aware-find-talkgroup.patch. Written as
            # ints in Hz; 0/0 disables the check.
            'freq_min': int(lo),
            'freq_max': int(hi),
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
            'crypt_keys': keys,
            'crypt_behavior': crypt_behavior,
            # Read by tk_p25.py's tune_data_receivers: ONLY channels marked
            # here are moved by an SNDCP data grant (TSBK 0x14). Everything
            # else keeps taking voice grants exactly as before, so the data
            # path cannot regress voice coverage.
            'data_only': data_only,
        }

    for leg in legs:
        radio = leg['radio']
        if_rate = if_rate_for(leg['rate'])
        # The reachable range is the demodulator's own bound, not the leg's
        # channel list: a failover control channel or a newly-announced voice
        # channel inside the window must still be reachable.
        half = usable_half_span(leg['rate'], usable_bw, if_rate)
        lo, hi = leg['centre'] - half, leg['centre'] + half
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
                                 cc_whitelist, port, if_rate, lo, hi))
            port += 2
        for i in range(leg['n_voice']):
            start = leg['voice'][i % len(leg['voice'])]
            channels.append(chan(f"VC{leg['name']}_{i}", radio, start,
                                 whitelist, port, if_rate, lo, hi))
            port += 2
        # The SNDCP data receiver, PINNED. It must never chase a voice grant:
        # data bursts are short and a receiver that wandered off would miss them.
        #
        # Pinning is done by giving it a trunking_sysname no trunking system
        # claims. tk_p25.py:144 looks the name up in self.systems; on a miss it
        # leaves rx_rcvr None (:156) and builds a 'Conventional' conv_state
        # (:160), and only a p25_receiver is ever handed a grant. So the channel
        # decodes its fixed frequency forever. This is the same mechanism the CC
        # receiver uses via an impossible whitelist, but stronger: that one is
        # still a trunking receiver that merely never matches, while this one is
        # not a trunking receiver at all.
        #
        # op25 logs "Receiver '<name>' configured with unknown trunking_sysname"
        # once at startup. That line is EXPECTED and is the marker that the pin
        # took; its absence means this channel is chasing voice.
        for i, freq in enumerate(leg.get('data', [])):
            channels.append(chan(f"DATA{leg['name']}_{i}", radio, freq,
                                 '', port, if_rate, lo, hi,
                                 sysname=f'{sysname}-DATA-CONV',
                                 data_only=True))
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
                f"device {dev['name']} runs {dev['rate']} Hz, which wants "
                f"{want}; every such channel pays an arb_resampler")

    for name, dev in by_name.items():
        leg = leg_by_radio[name]
        limit = usable_half_span(dev['rate'], dev['usable_bw_pct'],
                                 if_rate_for(dev['rate']))
        for f in leg_freqs(leg):
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

    for ch in cfg['channels']:
        dev = by_name[ch['device']]
        leg = leg_by_radio[dev['name']]
        half = usable_half_span(dev['rate'], dev['usable_bw_pct'],
                                if_rate_for(dev['rate']))
        want = (int(dev['frequency'] - half), int(dev['frequency'] + half))
        got = (ch['freq_min'], ch['freq_max'])
        if got != want:
            raise ValueError(
                f"channel {ch['name']} declares reachable range {got} but its "
                f"device window is {want}; op25 would skip grants it can tune, "
                f"or claim grants it cannot")
        for f in leg_freqs(leg):
            if not (ch['freq_min'] <= f <= ch['freq_max']):
                raise ValueError(
                    f"channel {ch['name']} cannot reach {f/1e6:.5f} MHz, which "
                    f"is in leg {leg['name']}'s own channel list")

    ports = sorted(int(ch['destination'].rsplit(':', 1)[1])
                   for ch in cfg['channels'])
    for a, b in zip(ports, ports[1:]):
        if b - a < 2:
            raise ValueError(
                f'UDP ports {a} and {b} are less than 2 apart; op25 sends on '
                f'port+slot_id and the recorder binds port and port+1')
    # The ceiling half of the same budget -- see PORT_BLOCK_SPAN above. Checked
    # here, where the ports are, rather than left to the two comments in
    # capture_control.py and start.post.ts that were the only record of it.
    span = ports[-1] - ports[0]
    if span > PORT_BLOCK_SPAN:
        raise ValueError(
            f'this config spans UDP ports {ports[0]}-{ports[-1]} ({span} wide, '
            f'{len(ports)} channels) but the block reserved for it is only '
            f'{PORT_BLOCK_SPAN} wide ({BASE_PORT}-{LAST_PORT} at the default '
            f'base port). Either widen the block in ALL THREE places that '
            f'state it -- here, scripts/capture_control.py MAX_VOICE and '
            f'server/api/listen/start.post.ts MAX_VOICE -- or use fewer '
            f'channels. Silently running past it hands a recorder a port some '
            f'other service may own.')


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
    ap.add_argument('--crypt-keys', default=None,
                    help='op25 crypt_keys JSON for the voice channels. Defaults '
                         'to lwin_keys.json when it exists, else no keys. A '
                         'WRONG key does not fail safe: RC4 with the wrong key '
                         'yields random plaintext, so op25 decodes loud garbage '
                         'instead of silencing the burst.')
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

    # Defaults to lwin_keys.json when present. Explicit --crypt-keys '' opts
    # out, and a missing file is silently no-keys, which is the safe direction:
    # without it op25 leaves encrypted bursts silenced, as it does today.
    keys = a.crypt_keys
    if keys is None:
        # This script lives in scripts/, so the tree root is its parent. Not a
        # hardcoded path: the repo is checked out in worktrees too.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default_keys = os.path.join(root, 'lwin_keys.json')
        keys = default_keys if os.path.exists(default_keys) else ''
    if keys:
        sys.stderr.write('make_multirx_cfg: voice channels reference %s\n' % keys)

    cfg = build(legs, crypt_keys=keys,
                whitelist=a.whitelist, cc_whitelist=a.cc_whitelist,
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
