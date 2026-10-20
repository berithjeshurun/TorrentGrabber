package com.prajwalch.torrentsearch.ui.bookmarks.component

import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.text.TextStyle

import com.prajwalch.torrentsearch.R

@Composable
fun BookmarksCount(
    totalBookmarksCount: Int,
    currentBookmarksCount: Int,
    modifier: Modifier = Modifier,
    color: Color = LocalContentColor.current,
    style: TextStyle = LocalTextStyle.current,
) {
    Text(
        modifier = modifier,
        text = pluralStringResource(
            R.plurals.bookmarks_count_format,
            totalBookmarksCount,
            currentBookmarksCount,
        ),
        color = color,
        style = style,
    )
}