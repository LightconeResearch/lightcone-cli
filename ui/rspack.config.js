// Inspired from JupyterLab's examples/app/rspack.config.js (BSD-3-Clause,
// Copyright (c) Jupyter Development Team).
const path = require('path');
const rspack = require('@rspack/core');
const Build = require('@jupyter/builder').Build;
const miniSVGDataURI = require('mini-svg-data-uri');
const data = require('./package.json');

const app = path.resolve(__dirname, '../src/lightcone/app');
const output = path.join(app, 'static');

// Copies the core plugins' settings schemas and themes beside the app, where
// LightconeApp serves them from.
const assets = Build.ensureAssets({
  packageNames: data.jupyterlab.extensions,
  output,
  schemaOutput: app,
  themeOutput: app
});

module.exports = (env, { mode = 'development' }) => [
  {
    entry: ['./lib/index.js'],
    output: { path: output, filename: 'bundle.js' },
    bail: true,
    // The wheel ships the production build, without its 10 MB source map.
    devtool: mode === 'production' ? false : 'source-map',
    mode,
    // Served from localhost, once per session: size hints are for the web.
    performance: { hints: false },
    module: {
      rules: [
        { test: /\.css$/, use: ['style-loader', 'css-loader'] },
        { test: /\.raw\.css$/, type: 'asset/source' },
        { test: /\.html$/, type: 'asset/resource' },
        { test: /\.md$/, type: 'asset/source' },
        { test: /\.(jpg|png|gif)$/, type: 'asset/resource' },
        { test: /\.js.map$/, type: 'asset/resource' },
        { test: /\.(woff2?|ttf)(\?v=\d+\.\d+\.\d+)?$/, type: 'asset' },
        { test: /\.eot(\?v=\d+\.\d+\.\d+)?$/, type: 'asset/resource' },
        {
          // In .css files, svg is loaded as a data URI.
          test: /\.svg(\?v=\d+\.\d+\.\d+)?$/,
          issuer: /\.css$/,
          type: 'asset/inline',
          generator: {
            dataUrl: {
              content: content => miniSVGDataURI(content.content),
              mimetype: 'image/svg+xml'
            }
          }
        },
        {
          // In compiled .ts files, svg is loaded as a raw string.
          test: /\.svg(\?v=\d+\.\d+\.\d+)?$/,
          issuer: /\.js$/,
          type: 'asset/source'
        }
      ]
    },
    plugins: [
      new rspack.DefinePlugin({
        // Needed for packages using cwd(), like the path polyfill.
        process: { cwd: () => '/', env: {} }
      })
    ]
  }
].concat(assets);
