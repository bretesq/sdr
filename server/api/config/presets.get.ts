export default defineEventHandler(() => {
  return {
    success: true,
    data: {
      presets: [
        { value: 'pd',          label: 'Police / Sheriff Dispatch' },
        { value: 'pd-all',      label: 'Police — Dispatch + Talk + Tac' },
        { value: 'fire',        label: 'Fire Dispatch' },
        { value: 'fire-all',    label: 'Fire — Dispatch + Tac + Talk' },
        { value: 'ems',         label: 'EMS + Hospital' },
        { value: 'interop',     label: 'Interop / Emergency Ops' },
        { value: 'schools',     label: 'Schools' },
        { value: 'publicworks', label: 'Public Works' },
        { value: 'all',         label: 'All Baton Rouge Area' },
      ],
      areas: [
        { value: 'br',  label: 'Baton Rouge Area' },
        { value: 'all', label: 'Statewide' },
      ],
    },
  }
})
