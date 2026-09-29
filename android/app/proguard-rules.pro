# Keep the JS bridge methods that WebView calls via reflection.
-keepclassmembers class com.vdograbber.app.MainActivity$Bridge {
    @android.webkit.JavascriptInterface <methods>;
}
