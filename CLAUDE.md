# Android Security Research - CLAUDE.md

This project discovers, downloads, and decompiles APKs from Google-related developers for security research. When analyzing decompiled source code, follow the vulnerability scope and research patterns below.

## Qualifying Vulnerabilities

### Arbitrary Code Execution (ACE)

Allows an attacker to execute arbitrary code in the context of the vulnerable application. The ACE must allow running native code on a user's device without user knowledge or permission, in the same process as the affected app (no requirement to bypass the OS sandbox).

**Qualifying examples:**
- Attacker gaining full control of the application (code downloaded from network and executed)
- Overwriting a `.so` file with a malicious `.so` that is executed by the victim app
- Executing Java code to call `exec` and run arbitrary native code

**Does NOT qualify:** Tricking a user into installing an app and executing code within that app itself.

### Theft of Sensitive Data

Unauthorized access to sensitive data from an app on an Android device.

**High Impact Data:**
- Login credentials or authentication tokens that can perform sensitive state-changing actions resulting in non-trivial damage
- Government IDs, medical information, payment information

**Low Impact Data:**
- Contact lists, photos (unless public by default), message content (email, IM, SMS), call/SMS logs, web history, browser bookmarks
- Information linked or linkable to an individual (educational, employment info)

**Does NOT qualify:**
- Location information alone (unless combined with ability to uniquely identify an individual)
- Access to non-sensitive internal files of another app

### Additional In-Scope Vulnerability Types

These are not strictly in scope but qualify if shown to have security impact. Typically security weaknesses used in conjunction with other vulnerabilities for exploit chains:

- Path traversal / zip path traversal leading to arbitrary file write
- Intent redirections leading to launching non-exported application components
- Vulnerabilities caused by unsafe usage of pending intents
- Orphaned permissions

### Non-Qualifying Issues

- Common low-risk vulnerabilities deemed trivially exploitable
- Access to non-sensitive media in external storage
- Strandhogg and Tapjacking variants
- Vulnerabilities that do not work on latest OS version
- Hardcoded API keys
- Attacks requiring a rooted device
- Secondary lockscreen bypasses
- Scenarios requiring unreasonable user interaction or social engineering

---

## Vulnerability Patterns to Search For

### 1. Intent Redirection / Proxy

The most common high-severity Android vulnerability pattern. An exported component extracts an Intent from extras and passes it to `startActivity()`/`startService()`/`sendBroadcast()`, allowing attackers to launch non-exported components.

**Grep patterns:**
```
getParcelableExtra(         -- Intent extras passed to startActivity/startService
startActivity((Intent)      -- direct cast and launch of attacker-controlled Intent
setResult(-1, getIntent())  -- returning attacker's Intent as activity result with URI permissions
setResult(RESULT_OK, getIntent())
```

### 2. Content Provider Attacks

**Path traversal:** `Uri.getLastPathSegment()` and `Uri.getPathSegments()` auto-decode `%2F` to `/`. URI like `content://authority/..%2Fshared_prefs%2Fsecrets.xml` bypasses path checks.

**Grep patterns:**
```
uri.getLastPathSegment()    -- auto-decodes %2F, enables path traversal
uri.getPathSegments()       -- same decoding issue
new File(root, uri.getPath())  -- direct path construction from URI
openFile(                   -- ContentProvider file operations
grantUriPermissions="true"  -- overly broad URI permission grants
ParcelFileDescriptor.open(  -- file access from URI-derived paths
```

**Also check:**
- `<root-path>` or `path=""` in `res/xml/*_paths.xml` (FileProvider exposing entire filesystem)
- Read/write permission mismatches on providers
- SQL injection in unprotected providers: `UNION SELECT * FROM SensitiveTable`
- `_display_name` from ContentProvider cursor used in file paths without sanitization

### 3. WebView Vulnerabilities

**Grep patterns:**
```
webView.loadUrl(            -- loading external URL, especially with auth headers
loadDataWithBaseURL(        -- attacker-controlled base URL = universal XSS
evaluateJavascript(         -- JS injection via string concatenation
Intent.parseUri(            -- intent:// scheme handling in WebView
shouldOverrideUrlLoading    -- custom URL routing logic
shouldInterceptRequest(     -- custom resource interception, path traversal risk
setAllowFileAccess(true)    -- file:// access enabled
setAllowUniversalAccessFromFileURLs  -- UXSS via file scheme
addJavascriptInterface(     -- JavaScript bridge exploitation
```

**Host validation bypasses:**
- `javascript://legitimate.com/%0aalert(1)` -- scheme bypass
- `file://legitimate.com/sdcard/exploit.html` -- file scheme bypass
- `https://attacker.com\\@legitimate.com` -- backslash bypass (API 1-24)
- `url.contains("legitimate.com")` -- substring check bypass
- Crafted `HierarchicalUri` objects where `getHost()` lies but `toString()` resolves to attacker domain

### 4. Dynamic Code Loading Hijack

Apps that load code from writable paths can be hijacked for persistent code execution.

**Grep patterns:**
```
System.load(                -- loading native libraries from paths
System.loadLibrary(         -- standard library loading
DexClassLoader              -- dynamic DEX loading
PathClassLoader             -- class loading from paths
loadClass(                  -- runtime class loading
splitcompat/                -- Play Core split APK directories
verified-splits/            -- trusted code loading paths
createPackageContext(        -- CONTEXT_INCLUDE_CODE flag = code loading from other packages
CONTEXT_INCLUDE_CODE        -- unsafe context creation
CONTEXT_IGNORE_SECURITY     -- bypasses security checks on package context
```

**Chain pattern:** Exported component -> file write via ContentProvider -> overwrite `.so` or DEX in app directory -> `System.load()` on next launch -> persistent ACE.

### 5. Implicit Intent Interception

Sensitive data sent via implicit intents can be intercepted by any app with a matching intent-filter.

**Grep patterns:**
```
sendBroadcast(new Intent(   -- implicit broadcast without explicit package
startActivity(new Intent(   -- implicit activity launch
startActivityForResult(     -- results can be spoofed by intercepting app
FLAG_GRANT_READ_URI_PERMISSION   -- URI permissions on implicit intents
FLAG_GRANT_WRITE_URI_PERMISSION
```

### 6. Permission Issues

**Grep patterns:**
```
<permission android:name=   -- check for missing protectionLevel (defaults to normal)
android:exported="true"     -- exported without android:permission attribute
android:uses-permission=    -- WRONG attribute on components (should be android:permission)
registerReceiver(           -- dynamic receivers without permission argument
```

**Also check:**
- Permission name typos between declaration and enforcement
- Ecosystem gaps: permission declared in app X, enforced in app Y, but app X not installed
- `Binder.getCallingUid()` checks confused by proxy calls

### 7. Shared UID Exploitation

**Grep patterns:**
```
android:sharedUserId="android.uid.system"     -- system-level privilege sharing
android:sharedUserId="android.uid.bluetooth"  -- bluetooth privilege sharing
android:sharedUserId=       -- any shared UID declaration
```

Apps sharing a UID share all resources. Compromising one weak app in the group chains into all others.

### 8. Memory Corruption via Deserialization

**Grep patterns:**
```
private long ptr            -- native pointer without transient modifier
finalize()                  -- calling freePtr() on attacker-controlled pointer
gson.fromJson(              -- dynamic class instantiation from untrusted JSON
Parcelable.Creator          -- deserialization of attacker-controlled parcels
```

### 9. Arbitrary File Operations

**Grep patterns:**
```
getExternalCacheDir()       -- world-readable cache writes
getExternalStorageDirectory()  -- SD card writes without access control
new FileOutputStream(       -- file write with path from untrusted source
new FileInputStream(        -- file read with path from untrusted source
ZipEntry.getName()          -- zip path traversal (../  in entry names)
```

---

## Manifest Analysis Checklist

When examining `AndroidManifest.xml` of decompiled apps, check:

1. **Exported components** without permission guards (`android:exported="true"` without `android:permission`)
2. **`android:sharedUserId`** declarations -- map all apps sharing that UID
3. **FileProvider `_paths.xml`** -- look for `<root-path>` or `path=""`
4. **Custom permissions** without `android:protectionLevel="signature"`
5. **`android:grantUriPermissions="true"`** on content providers
6. **`android:priority="999"`** on intent filters (used for interception)
7. **Deep link intent-filters** with `android.intent.category.BROWSABLE` and broad schemes

---

## Common Exploit Chains

1. **Intent redirect -> FileProvider -> .so overwrite -> ACE:** Exported activity accepts Intent extra -> redirected to ContentProvider with write permission -> write malicious `.so` to app lib directory -> loaded on next app launch

2. **Implicit intent -> URI permission -> file theft:** Intercept implicit intent with `FLAG_GRANT_READ_URI_PERMISSION` -> gain access to victim's ContentProvider -> read sensitive files

3. **WebView URL load -> JavaScript injection -> cookie/file theft:** Exported activity loads attacker URL in WebView -> XSS or `loadDataWithBaseURL` with attacker origin -> steal cookies or read local files via XHR

4. **Path traversal -> code write -> dynamic load -> persistent ACE:** Content Provider path traversal via `%2F` encoding -> write DEX/SO to `verified-splits` or lib directory -> auto-loaded by ClassLoader on app start

5. **Permission gap -> broadcast -> sensitive data:** Custom permission declared without `protectionLevel` -> attacker declares same permission -> accesses protected broadcast receiver -> receives sensitive data

---

## References

- https://blog.oversecured.com/Disclosure-of-7-Android-and-Google-Pixel-Vulnerabilities/
- https://blog.oversecured.com/Two-weeks-of-securing-Samsung-devices-Part-1/
- https://blog.oversecured.com/Two-weeks-of-securing-Samsung-devices-Part-2/
- https://blog.oversecured.com/Oversecured-detects-dangerous-vulnerabilities-in-the-TikTok-Android-app/
- https://blog.oversecured.com/20-Security-Issues-Found-in-Xiaomi-Devices/
- https://blog.oversecured.com/Content-Providers-and-the-potential-weak-spots-they-can-have/
- https://blog.oversecured.com/Discovering-vendor-specific-vulnerabilities-in-Android/
- https://blog.oversecured.com/Android-security-checklist-theft-of-arbitrary-files/
- https://blog.oversecured.com/Android-security-checklist-webview/
- https://blog.oversecured.com/Common-mistakes-when-using-permissions-in-Android/
- https://blog.oversecured.com/Why-dynamic-code-loading-could-be-dangerous-for-your-apps-a-Google-example/
- https://blog.oversecured.com/Android-Exploring-vulnerabilities-in-WebResourceResponse/
- https://blog.oversecured.com/Exploiting-memory-corruption-vulnerabilities-on-Android/
- https://blog.oversecured.com/Gaining-access-to-arbitrary-Content-Providers/
- https://blog.oversecured.com/Evernote-Universal-XSS-theft-of-all-cookies-from-all-sites-and-more/
- https://blog.oversecured.com/Interception-of-Android-implicit-intents/
- https://blog.oversecured.com/Oversecured-automatically-discovers-persistent-code-execution-in-the-Google-Play-Core-Library/
- https://blog.oversecured.com/Android-Access-to-app-protected-components/
- https://blog.oversecured.com/Android-arbitrary-code-execution-via-third-party-package-contexts/
- https://ndevtk.github.io/writeups/2024/08/01/awas/
