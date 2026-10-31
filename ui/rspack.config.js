const path = require('path');
const rspack = require('@rspack/core');

// One build, the one the wheel ships: production, without a source map.
module.exports = {
  entry: './lib/index.js',
  output: {
    path: path.resolve(__dirname, '../src/lightcone/app/static'),
    filename: 'bundle.js',
    // Jupyter serves this directory as immutable, so a lazy chunk (pdf.js and
    // its worker) is named by its content; bundle.js is versioned by the page.
    chunkFilename: '[contenthash].js'
  },
  bail: true,
  mode: 'production',
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
    // The Lightcone mark, as lightconeresearch.org uses it; the page links it.
    new rspack.CopyRspackPlugin({ patterns: ['style/favicon.svg'] }),
    new rspack.DefinePlugin({
      // Needed for packages using cwd(), like the path polyfill.
      process: { cwd: () => '/', env: {} }
    })
  ]
};
