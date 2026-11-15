using System;
using System.Collections.Generic;
using System.Linq;

namespace MusicBox.Desktop.Services;

public enum PlaybackMode { Sequential, RepeatAll, RepeatOne, Shuffle }

/// <summary>Queue navigation, independent of network/media state for deterministic tests.</summary>
public sealed class PlaybackQueue
{
    private readonly List<Track> _items = new();
    private readonly Stack<int> _history = new();
    private readonly Func<int, int> _pick;
    public PlaybackQueue(Func<int, int>? pick = null) => _pick = pick ?? Random.Shared.Next;
    public IReadOnlyList<Track> Items => _items;
    public int Index { get; private set; } = -1;
    public PlaybackMode Mode { get; set; }
    public Track? Current => Index >= 0 && Index < _items.Count ? _items[Index] : null;
    public void Replace(IEnumerable<Track> items, Track selected)
    {
        _items.Clear(); _items.AddRange(items); _history.Clear();
        Index = _items.FindIndex(t => t.Provider == selected.Provider && t.Id == selected.Id);
        if (Index < 0) { _items.Add(selected); Index = _items.Count - 1; }
    }
    public Track? Select(int index)
    {
        if (index < 0 || index >= _items.Count) return null;
        if (Index >= 0 && Index != index) _history.Push(Index);
        Index = index; return Current;
    }
    public Track? Next(bool ended = false)
    {
        if (Current is null) return null;
        if (ended && Mode == PlaybackMode.RepeatOne) return Current;
        if (Mode == PlaybackMode.Shuffle && _items.Count > 1)
        {
            var choice = _pick(_items.Count - 1); if (choice >= Index) choice++;
            return Select(choice);
        }
        if (Index + 1 < _items.Count) return Select(Index + 1);
        return Mode == PlaybackMode.RepeatAll ? Select(0) : null;
    }
    public Track? Previous()
    {
        if (Current is null) return null;
        if (Mode == PlaybackMode.Shuffle && _history.Count > 0) { Index = _history.Pop(); return Current; }
        return Select(Index > 0 ? Index - 1 : Mode == PlaybackMode.RepeatAll ? _items.Count - 1 : 0);
    }
    public void Remove(int index)
    {
        if (index < 0 || index >= _items.Count || index == Index) return;
        _items.RemoveAt(index); if (index < Index) Index--; _history.Clear();
    }
    public void KeepCurrent()
    {
        var current = Current; _items.Clear(); _history.Clear();
        if (current is null) Index = -1; else { _items.Add(current); Index = 0; }
    }
}
