package com.prajwalch.torrentsearch.ui.extension

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.core.net.toUri

/**
 * Attempts to open the given magnet URI.
 *
 * @return `true` if the client is found, `false` otherwise.
 */
fun Context.openMagnetLink(magnetUri: String): Boolean {
    return try {
        val torrentClientOpenIntent = Intent(Intent.ACTION_VIEW, magnetUri.toUri())
        startActivity(torrentClientOpenIntent)
        true
    } catch (_: ActivityNotFoundException) {
        false
    }
}

/** Starts the application chooser to share the text with. */
fun Context.startTextShareIntent(text: String) {
    try {
        val sendIntent = Intent().apply {
            action = Intent.ACTION_SEND
            type = "text/plain"

            putExtra(Intent.EXTRA_TEXT, text)
        }
        val shareIntent = Intent.createChooser(sendIntent, null)

        startActivity(shareIntent)
    } catch (_: ActivityNotFoundException) {
        // Do nothing.
    }
}