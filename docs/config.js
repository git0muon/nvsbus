/* Deployment settings for the static (GitHub Pages) build.
 *
 * The tracking token below is a PUBLIC share token -- the same one that appears in the
 * Compass tracking URL. Anyone who can open this site can read it, and anyone who has it
 * can already see the same fleet on the Compass page. Do not put anything secret here.
 */
window.TRACKER_CONFIG = {
  // Compass public-tracking share token.
  token: "4996b81acfbfc13d3f539a5dff6c6fdbed921b3aa46f9c50",

  // Supabase project that hosts the public-tracking function.
  supabaseUrl: "https://supabase.apps2db.uctechlabs.com",

  // Public anonymous key of that project. Shipped in the Compass web app itself and, by
  // design, safe to expose: it only grants what row-level security allows.
  supabaseAnonKey:
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzdXBhYmFzZSIsImlhdCI6MTc3MTc1ODU0MCwi" +
    "ZXhwIjo0OTI3NDMyMTQwLCJyb2xlIjoiYW5vbiJ9.0OKRMEyYPy-8bjw0hWCugu8LZTn3k27y9A6xjG0Ek1w",

  // How often to poll, in seconds. The Compass page uses 30; 10 feels livelier.
  refreshSeconds: 10,

  // A sample older than this many hours counts as offline (matches Compass).
  offlineAfterHours: 3
};
