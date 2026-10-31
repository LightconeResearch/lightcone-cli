import { OutputProvenance } from '@astra-spec/ui/components';
import type { OutputRun } from '@astra-spec/ui/model';
import { useEffect, useState } from 'react';

import { errorMessage, getJson } from './api';

type Recorded = { run?: OutputRun | null; error?: string };

/**
 * The run lc recorded for the output at `path`, read by lc itself on the
 * server. Mounted only for the open output detail, so no other run is read;
 * its key carries the output's cache token, so a new run remounts it.
 */
export function Provenance({ path }: { path: string | undefined }) {
  const [recorded, setRecorded] = useState<Recorded>(path ? {} : { run: null });
  useEffect(() => {
    if (!path) return;
    let live = true;
    getJson<Recorded>('run', new URLSearchParams({ path }))
      .catch((error: unknown) => ({ error: errorMessage(error) }))
      .then(value => {
        if (live) setRecorded(value);
      });
    return () => {
      live = false;
    };
  }, []);
  return <OutputProvenance {...recorded} />;
}
