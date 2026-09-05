// Firebase web-app config for cross-device sync of the plan and settings.
// These values identify the project; access is controlled by Firestore rules
// (see firestore.rules), not by keeping them secret. Set to null to run the
// site without sync.
window.FPL_FIREBASE = {
  apiKey: "AIzaSyAu69XFVWzT5b1VNV4Qkx4K9bj5oM-4I4A",
  authDomain: "fpl-picker-8033b.firebaseapp.com",
  projectId: "fpl-picker-8033b",
  storageBucket: "fpl-picker-8033b.firebasestorage.app",
  messagingSenderId: "53463752631",
  appId: "1:53463752631:web:0dba60aa793e4a4b2b1232"
};

// Cloudflare Worker that proxies questions to Claude, so the API key never
// reaches the browser. Deploy worker/ and paste the URL here to switch the
// Ask box on; leave null and the app simply does not offer it.
window.FPL_WORKER = null;
