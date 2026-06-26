const path = require('path');
const webpack = require('webpack');
const { UserscriptPlugin } = require('webpack-userscript');

module.exports = {
  mode: 'production',
  entry: {
    browseruse: './js/browseruse.user.js'
  },
  output: {
    path: path.resolve(__dirname, 'dist')
  },
  optimization: {
    minimize: true
  },
  devServer: {
    allowedHosts: 'all',
    client: {
      overlay: false
    },
    static: {
      directory: path.join(__dirname, 'dist')
    },
    webSocketServer: false
  },
  plugins: [
    new webpack.ProgressPlugin(),
    new UserscriptPlugin({
      metajs: false,
      headers(_, { fileInfo: { basename } }) {
        const packages = {
          browseruse: {
            name: 'Visual BrowserUse',
            include: '*',
            grant: ['none'],
            'run-at': 'document-end'
          }
        };

        return packages[basename];
      }
    })
  ]
};
