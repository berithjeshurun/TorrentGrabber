package com.prajwalch.torrentsearch.ui.bookmarks

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope

import com.prajwalch.torrentsearch.data.repository.BookmarkRepository
import com.prajwalch.torrentsearch.data.repository.SettingsRepository
import com.prajwalch.torrentsearch.domain.model.SortCriteria
import com.prajwalch.torrentsearch.domain.model.SortOptions
import com.prajwalch.torrentsearch.domain.model.SortOrder
import com.prajwalch.torrentsearch.domain.model.Torrent
import com.prajwalch.torrentsearch.filter.TorrentFilters
import com.prajwalch.torrentsearch.util.createSortComparator

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.WhileSubscribed
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

import org.koin.core.annotation.KoinViewModel

import java.io.InputStream
import java.io.OutputStream

import kotlin.time.Duration.Companion.seconds

/** UI state for the Bookmarks screen. */
data class BookmarksUiState(
    val bookmarksState: BookmarksState = BookmarksState.Loading,
    val totalBookmarksCount: Int = 0,
    val sortOptions: SortOptions = SortOptions(),
    val showSwipeDeleteTip: Boolean = true,
)

sealed interface BookmarksState {
    data object Loading : BookmarksState

    data object Empty : BookmarksState

    data object EmptyNoMatches : BookmarksState

    data class Ready(val bookmarks: List<Torrent>) : BookmarksState
}

/** ViewModel that handles the business logic of Bookmarks screen. */
@KoinViewModel
class BookmarksViewModel(
    private val bookmarkRepository: BookmarkRepository,
    private val settingsRepository: SettingsRepository,
    private val savedStateHandle: SavedStateHandle,
) : ViewModel() {
    private val filterQuery = savedStateHandle.getStateFlow(KEY_FILTER_QUERY, initialValue = "")

    private val bookmarksState: Flow<BookmarksState> =
        combine(
            bookmarkRepository.getAllBookmarks(),
            settingsRepository.enableNSFWMode,
            filterQuery,
        ) { bookmarks, nsfwModeEnabled, filterQuery ->
            if (bookmarks.isEmpty()) return@combine BookmarksState.Empty

            val filteredBookmarks = bookmarks
                .filterIf(!nsfwModeEnabled, TorrentFilters.isSfw())
                .filterIf(filterQuery.isNotBlank(), TorrentFilters.matchesQuery(filterQuery))

            if (filteredBookmarks.isEmpty()) {
                BookmarksState.EmptyNoMatches
            } else {
                BookmarksState.Ready(filteredBookmarks)
            }
        }

    val uiState: StateFlow<BookmarksUiState> =
        combine(
            bookmarksState,
            bookmarkRepository.getBookmarksCount(),
            settingsRepository.bookmarksSortOptions,
            settingsRepository.showBookmarkSwipeDeleteTip,
        ) {
                bookmarksState,
                totalBookmarksCount,
                sortOptions,
                showSwipeDeleteTip,
            ->
            val finalBookmarksState = if (bookmarksState is BookmarksState.Ready) {
                bookmarksState.bookmarks
                    .sortedWith(createSortComparator(sortOptions.criteria, sortOptions.order))
                    .let(BookmarksState::Ready)
            } else {
                bookmarksState
            }

            BookmarksUiState(
                bookmarksState = finalBookmarksState,
                totalBookmarksCount = totalBookmarksCount,
                sortOptions = sortOptions,
                showSwipeDeleteTip = showSwipeDeleteTip,
            )
        }.stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5.seconds),
            initialValue = BookmarksUiState(),
        )

    fun hideSwipeToDeleteTip() {
        viewModelScope.launch {
            settingsRepository.showBookmarkSwipeDeleteTip(false)
        }
    }

    /** Deletes bookmark associated with the given ID. */
    fun deleteBookmarkById(id: String) {
        viewModelScope.launch {
            bookmarkRepository.deleteBookmarkById(id)
        }
    }

    /** Deletes all bookmarks. */
    fun deleteAllBookmarks() {
        viewModelScope.launch {
            bookmarkRepository.deleteAllBookmarks()
        }
    }

    /** Sets or updates the sort criteria. */
    fun setSortCriteria(criteria: SortCriteria) {
        viewModelScope.launch {
            settingsRepository.setBookmarksSortCriteria(criteria)
        }
    }

    /** Sets or updates the sort order. */
    fun setSortOrder(order: SortOrder) {
        viewModelScope.launch {
            settingsRepository.setBookmarksSortOrder(order)
        }
    }

    /** Filters the bookmarks using the given query. */
    fun filterBookmarks(query: String) {
        savedStateHandle[KEY_FILTER_QUERY] = query
    }

    /** Attempts to import bookmarks from the given stream. */
    fun importBookmarks(inputStream: InputStream) {
        viewModelScope.launch {
            bookmarkRepository.importBookmarks(inputStream = inputStream)
        }
    }

    /** Attempts to export bookmarks to the given stream. */
    fun exportBookmarks(outputStream: OutputStream) {
        viewModelScope.launch {
            bookmarkRepository.exportBookmarks(outputStream = outputStream)
        }
    }

    private companion object {
        private const val KEY_FILTER_QUERY = "filter_query"
    }
}

private fun List<Torrent>.filterIf(
    condition: Boolean,
    predicate: (Torrent) -> Boolean,
): List<Torrent> {
    return if (condition) this.filter(predicate) else this
}