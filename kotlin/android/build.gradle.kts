// laching.android: the Android half of the shell, thin on purpose. Play Billing behind StoreBackend, SharedPreferences
// behind SettingsStore, exact alarms and notification plumbing for the reminders, the reduce-motion check, the
// screenshot run and the launch extras the tools pass. Ships no strings and no resources: the app injects every piece
// of copy.
plugins {
    alias(libs.plugins.android.library)
}

android {
    namespace = "laching.android"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // JVM unit tests of the pure pieces (`testDebugUnitTest`), on JUnit 5 like the JVM modules.
    testOptions {
        unitTests.all { it.useJUnitPlatform() }
    }
}

kotlin {
    compilerOptions {
        freeCompilerArgs.add("-Xexplicit-api=strict")
    }
}

dependencies {
    api(project(":core"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.billing)
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(platform(libs.junit.bom))
    testImplementation(kotlin("test-junit5"))
    testImplementation(libs.kotlinx.coroutines.test)
    testRuntimeOnly(libs.junit.platform.launcher)
}
