import { PageConfig } from '@jupyterlab/coreutils';

declare let __webpack_public_path__: string;

// Before any other module loads: assets resolve against the served bundle.
__webpack_public_path__ = PageConfig.getOption('fullStaticUrl') + '/';
