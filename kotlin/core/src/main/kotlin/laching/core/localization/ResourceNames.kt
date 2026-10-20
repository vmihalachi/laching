package laching.core.localization

import java.security.MessageDigest

/**
 * The resource names of catalog keys, as the apps' `l10n.py` writes them, and the text for a key the strings don't
 * have. The `.resw` name (`reswName`, `resw_name` in `l10n.py`): lower-case ASCII letters and digits stay, every other
 * UTF-16 code unit becomes `_` plus 4 upper-case hex digits; upper-case letters are escaped because MRT names ignore
 * case. A name over [MAX_LENGTH] keeps its first [PREFIX_LENGTH] characters plus `_h` and 16 hex digits of the key's
 * SHA-256, leaving room for a `__other` plural suffix. The Android name (`androidName`, `android_name`) is `s_` plus
 * the `.resw` name in lower case, so it starts with a letter, uses only `[a-z0-9_]` and is never a keyword.
 */
public object ResourceNames {
    public const val MAX_LENGTH: Int = 240
    public const val PREFIX_LENGTH: Int = 200

    // Every brace escaped: Android's ICU regex refuses a bare `}`, which the JVM's accepts.
    private val specifier = Regex("""%%|%(?:(\d+)\$)?(?:lld|ld|d|@)""")
    private val placeholder = Regex("""\{\{|\}\}|\{(\d+)\}""")

    /** The `.resw` name of [key]. */
    public fun reswName(key: String): String {
        val sb = StringBuilder()
        for (ch in key) {
            if (ch in 'a'..'z' || ch in '0'..'9') {
                sb.append(ch)
            } else {
                sb.append('_').append(
                    ch.code
                        .toString(16)
                        .uppercase()
                        .padStart(4, '0'),
                )
            }
        }
        if (sb.length <= MAX_LENGTH) return sb.toString()
        val hash = MessageDigest.getInstance("SHA-256").digest(key.toByteArray(Charsets.UTF_8))
        val hex = hash.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0') }
        return sb.substring(0, PREFIX_LENGTH) + "_h" + hex.substring(0, 16)
    }

    /** The Android string resource name of [key]: `s_` plus [reswName] in lower case. */
    public fun androidName(key: String): String = "s_" + reswName(key).lowercase()

    /**
     * The text for a key the strings don't have: English straight from the key (the Mac's catalog keys are the English
     * source), with Apple's format specifiers (`%lld`, `%@`, positional `%2$@`, `%%`) turned into the `{0}`
     * placeholders the exported strings use, so nothing ever shows up blank.
     */
    public fun fallbackText(key: String): String {
        var index = 0
        return specifier.replace(key.replace("{", "{{").replace("}", "}}")) { m ->
            when {
                m.value == "%%" -> "%"
                m.groups[1] != null -> "{${m.groupValues[1].toInt() - 1}}"
                else -> "{${index++}}"
            }
        }
    }

    /**
     * [template] with `{0}`, `{1}`… replaced by [args] (`{{` and `}}` are literal braces), the way .NET's
     * `string.Format` reads the exported strings. A placeholder without an argument stays as written.
     */
    public fun format(
        template: String,
        vararg args: Any?,
    ): String =
        placeholder.replace(template) { m ->
            when (m.value) {
                "{{" -> "{"
                "}}" -> "}"
                else -> args.getOrNull(m.groupValues[1].toInt())?.toString() ?: m.value
            }
        }
}
