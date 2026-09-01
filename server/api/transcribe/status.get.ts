import { isTranscriberRunning, isSttServerRunning } from '~/server/utils/transcriber'

export default defineEventHandler(async () => {
  // Two independent facts: a watcher can be running while the GPU server is
  // down, in which case it is transcribing on CPU at ~15x the cost per clip.
  return {
    success: true,
    data: {
      running: isTranscriberRunning(),
      gpuServer: await isSttServerRunning(),
    },
  }
})
