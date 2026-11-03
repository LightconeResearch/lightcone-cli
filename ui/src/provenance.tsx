import { OutputProvenance } from '@astra-spec/ui/components';
import type { OutputRun, OutputStatus } from '@astra-spec/ui/model';
import { useEffect, useState } from 'react';

import { errorMessage, getJson } from './api';

type Recorded = { run?: OutputRun | null; error?: string };

/**
 * The run lc recorded for the output at `path`, read by lc itself on the
 * server, beside the state lc classifies the output in. Mounted only for the
 * open output detail, so no other run is read; refreshed with the analysis,
 * including notifications for the run sidecar.
 */
export function Provenance({
  path,
  revision,
  status
}: {
  path: string | undefined;
  revision: object;
  status: OutputStatus | undefined;
}) {
  const [recorded, setRecorded] = useState<Recorded>(path ? {} : { run: null });
  useEffect(() => {
    if (!path) {
      setRecorded({ run: null });
      return;
    }
    let live = true;
    getJson<Recorded>('run', new URLSearchParams({ path }))
      .catch((error: unknown) => ({ error: errorMessage(error) }))
      .then(value => {
        if (live) setRecorded(value);
      });
    return () => {
      live = false;
    };
  }, [path, revision]);
  return <OutputProvenance status={status} {...recorded} />;
}
