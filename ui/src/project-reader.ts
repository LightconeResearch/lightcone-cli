import type { ProjectReader } from '@astra-spec/sdk';
import type { Contents } from '@jupyterlab/services';

type Listing = ReadonlyMap<string, Contents.IModel> | undefined;

/**
 * The project, read through Jupyter's Contents API. The server's root is the
 * project root, so the SDK's project-relative paths are contents paths as is.
 *
 * Paths are stat'ed from their parent's listing, and a directory is listed
 * only once its own parent has shown it exists: resolution asks about many
 * outputs that were never materialized, and this answers "absent" without
 * a failing request for each. Make one reader per resolution, so the next
 * one sees what changed.
 */
export function projectReader(contents: Contents.IManager): ProjectReader {
  const listings = new Map<string, Promise<Listing>>();
  const list = (dir: string): Promise<Listing> => {
    let listing = listings.get(dir);
    if (!listing) {
      listing = (async () => {
        if (dir) {
          const slash = dir.lastIndexOf('/');
          const parent = await list(dir.slice(0, Math.max(0, slash)));
          if (parent?.get(dir.slice(slash + 1))?.type !== 'directory') return undefined;
        }
        const model = await contents.get(dir, { type: 'directory', content: true });
        return new Map((model.content as Contents.IModel[]).map(entry => [entry.name, entry]));
      })();
      listings.set(dir, listing);
    }
    return listing;
  };

  return {
    async readText(path) {
      const model = await contents.get(path, { type: 'file', format: 'text', content: true });
      if (typeof model.content !== 'string') {
        throw new Error(`${path} is not a text file.`);
      }
      return model.content;
    },
    async stat(path) {
      if (!path) return { type: 'directory' };
      const slash = path.lastIndexOf('/');
      const entry = (await list(path.slice(0, Math.max(0, slash))))?.get(path.slice(slash + 1));
      if (!entry) return undefined;
      return entry.type === 'directory'
        ? { type: 'directory' }
        : { type: 'file', size: entry.size ?? 0, modifiedAtMs: Date.parse(entry.last_modified) };
    },
    async readDirectory(path) {
      const listing = await list(path);
      if (!listing) throw new Error(`No directory at ${path}.`);
      return [...listing.values()].map(({ name, type }) => ({
        name,
        type: type === 'directory' ? 'directory' : 'file'
      }));
    }
  };
}
