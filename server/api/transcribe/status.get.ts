import { isTranscriberRunning } from '~/server/utils/transcriber'

export default defineEventHandler(() => {
  return { success: true, data: { running: isTranscriberRunning() } }
})
