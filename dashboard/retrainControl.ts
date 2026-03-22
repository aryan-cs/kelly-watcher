import {postApiJson} from './api.js'

export interface RetrainRequestResult {
  ok: boolean
  message: string
}

export async function requestManualRetrain(): Promise<RetrainRequestResult> {
  return postApiJson<RetrainRequestResult>('/api/manual-retrain')
}
