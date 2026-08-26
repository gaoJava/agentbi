const path = require('path');
const { container } = require('webpack');
const extension = require('../extension.json');
const pkg = require('./package.json');

module.exports = (_env, argv) => {
  const production = argv.mode === 'production';
  return {
    entry: production ? {} : './src/index.tsx',
    mode: production ? 'production' : 'development',
    devServer: { port: 3010, headers: { 'Access-Control-Allow-Origin': '*' } },
    output: {
      clean: true,
      filename: production ? undefined : '[name].[contenthash].js',
      chunkFilename: '[name].[contenthash].js',
      path: path.resolve(__dirname, 'dist'),
      publicPath: `/api/v1/extensions/${extension.publisher}/${extension.name}/`,
    },
    resolve: { extensions: ['.ts', '.tsx', '.js'] },
    externalsType: 'window',
    externals: { '@apache-superset/core': 'superset' },
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          use: {
            loader: 'ts-loader',
            options: { compilerOptions: { noEmit: false } },
          },
          exclude: /node_modules/,
        },
      ],
    },
    plugins: [
      new container.ModuleFederationPlugin({
        name: 'agentbi_insightPilot',
        filename: 'remoteEntry.[contenthash].js',
        exposes: { './index': './src/index.tsx' },
        shared: {
          react: { singleton: true, requiredVersion: pkg.peerDependencies.react, import: false },
          'react-dom': {
            singleton: true,
            requiredVersion: pkg.peerDependencies['react-dom'],
            import: false,
          },
        },
      }),
    ],
  };
};
