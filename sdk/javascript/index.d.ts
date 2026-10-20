export declare const SDK_VERSION: string;

export declare class EngineError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, data: unknown);
}

export interface AddOptions {
  mode?: string;
  id?: string;
  peers?: Array<{ ip: string; port: number }>;
  selective_files?: number[];
}

export declare class TorrentEngine {
  baseUrl: string;
  constructor(
    baseUrl?: string,
    opts?: { fetch?: typeof fetch; headers?: Record<string, string> }
  );
  request(
    method: string,
    path: string,
    opts?: { body?: unknown; query?: Record<string, unknown> }
  ): Promise<any>;
  health(): Promise<any>;
  version(): Promise<any>;
  checkEngineUpdate(): Promise<any>;
  checkSdkUpdate(opts?: { manifestUrl?: string }): Promise<any>;
  session(): Promise<any>;
  metricsJson(): Promise<any>;
  add(magnetOrUrl: string, opts?: AddOptions): Promise<any>;
  list(): Promise<any>;
  status(id: string): Promise<any>;
  pause(id: string): Promise<any>;
  resume(id: string): Promise<any>;
  remove(id: string, opts?: { deleteFiles?: boolean }): Promise<any>;
  playhead(id: string, offsetBytes: number): Promise<any>;
  buffer(id: string): Promise<any>;
  boost(
    id: string,
    opts?: { duration?: number; multiplier?: number }
  ): Promise<any>;
  tracks(id: string): Promise<any>;
  startHls(id: string, params?: Record<string, unknown>): Promise<any>;
  setMediaFile(id: string, index: number): Promise<any>;
  mediaUrl(id: string): string;
  hlsPlaylistUrl(id: string, opts?: { master?: boolean }): string;
}

export default TorrentEngine;
