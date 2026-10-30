import { registerRootComponent } from 'expo';

import { installCrashGuard } from './src/diag';
import App from './App';

// Before the first render: an uncaught JS error must be reported to the server
// (Settings → Diagnostics, and /api/client-log) instead of closing the app.
installCrashGuard();

// registerRootComponent calls AppRegistry.registerComponent('main', () => App);
// It also ensures that whether you load the app in Expo Go or in a native build,
// the environment is set up appropriately
registerRootComponent(App);
