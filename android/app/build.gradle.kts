plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.vdograbber.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.vdograbber.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 35
        versionName = "1.9.9"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    // youtubedl-android ships big per-ABI native libs (yt-dlp + ffmpeg);
    // split APKs keep each download ~1/3 the size of a universal build.
    // CI renames and attaches the per-ABI APKs (android.yml).
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "armeabi-v7a", "x86_64")
            isUniversalApk = true
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    packaging {
        resources.excludes += "META-INF/*"
        // youtubedl-android exec()s the python/ffmpeg binaries it ships, so
        // they must be extracted to disk (nativeLibraryDir) - same as Seal
        jniLibs.useLegacyPackaging = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("com.google.android.material:material:1.12.0")
    // yt-dlp engine on-device (HLS/DASH + every yt-dlp-supported site);
    // version pinned to the same line Seal uses - verified on Maven Central
    implementation("io.github.junkfood02.youtubedl-android:library:0.18.1")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:0.18.1")
    testImplementation("junit:junit:4.13.2")
    // v1.9.9: the real org.json classes for JVM tests - the android.jar stub
    // throws "not mocked" (TelemetryParityTest needs JSONObject/put/get for
    // the origin-tag parity checks)
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
