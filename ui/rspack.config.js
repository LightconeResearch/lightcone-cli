const path = require('path');
const rspack = require('@rspack/core');

module.exports = (env, { mode = 'development' }) => ({
  entry: './lib/index.js',
  output: { path: path.resolve(__dirname, '../src/lightcone/app/static'), filename: 'bundle.js' },
  bail: true,
  // The wheel ships the production build, without its source map.
  devtool: mode === 'production' ? false : 'source-map',
  mode,
  // Served from localhost, once per session: size hints are for the web.
  performance: { hints: false },
  module: {
    rules: [
      { test: /\.css$/, use: ['style-loader', 'css-loader'] },
      // KaTeX's fonts, which astra-ui's math rendering loads.
      { test: /\.(woff2?|ttf)$/, type: 'asset/resource' }
    ]
  },
  plugins: [
    new rspack.DefinePlugin({
      // Needed for packages using cwd(), like the path polyfill.
      process: { cwd: () => '/', env: {} }
    })
  ]
});
