// Cross-platform key/value store.
//
// expo-secure-store has NO web implementation — calling it in a browser throws
// ("SecureStore is not available on web"), which was crashing app startup and
// leaving a white screen. On web we fall back to localStorage; on iOS/Android
// we keep the encrypted SecureStore. Every call is wrapped so a storage failure
// can never take down the whole app.
import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const isWeb = Platform.OS === 'web';

export async function getItem(key) {
  try {
    if (isWeb) {
      if (key === 'nn_refresh') return null;
      return globalThis.localStorage?.getItem(key) ?? null;
    }
    return await SecureStore.getItemAsync(key);
  } catch (e) {
    if (__DEV__) console.warn('[secureStore] getItem failed:', key, e?.message || e);
    return null;
  }
}

export async function setItem(key, value) {
  try {
    if (value == null) return;
    if (isWeb) {
      if (key === 'nn_refresh') return;
      globalThis.localStorage?.setItem(key, value);
      return;
    }
    await SecureStore.setItemAsync(key, value);
  } catch (e) {
    if (__DEV__) console.warn('[secureStore] setItem failed:', key, e?.message || e);
  }
}

export async function deleteItem(key) {
  try {
    if (isWeb) {
      globalThis.localStorage?.removeItem(key);
      return;
    }
    await SecureStore.deleteItemAsync(key);
  } catch (e) {
    if (__DEV__) console.warn('[secureStore] deleteItem failed:', key, e?.message || e);
  }
}
