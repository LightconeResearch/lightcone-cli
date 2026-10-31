import { URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';

/** GET one of the app's own routes (`lightcone/api/<route>`) as JSON. */
export async function getJson<T>(route: string, query: URLSearchParams): Promise<T> {
  const settings = ServerConnection.makeSettings();
  const url = `${URLExt.join(settings.baseUrl, 'lightcone/api', route)}?${query}`;
  const response = await ServerConnection.makeRequest(url, {}, settings);
  if (!response.ok) throw await ServerConnection.ResponseError.create(response);
  return (await response.json()) as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
