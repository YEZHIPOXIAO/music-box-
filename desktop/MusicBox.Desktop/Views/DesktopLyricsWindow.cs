using System;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using MusicBox.Desktop.Services;
using Microsoft.UI.Windowing;

namespace MusicBox.Desktop.Views;

/// <summary>Independent, closable native lyric window. No background timer.</summary>
public sealed class DesktopLyricsWindow : Window
{
    private readonly TextBlock _line = new() { Text = "等待歌词", FontSize = 26, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center, HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly TextBlock _next = new() { FontSize = 16, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center, Opacity = 0.65 };
    public DesktopLyricsWindow()
    {
        Title = "MusicBox · 桌面歌词";
        var panel = new StackPanel { Spacing = 8, Padding = new Thickness(20), VerticalAlignment = VerticalAlignment.Center };
        panel.Children.Add(_line); panel.Children.Add(_next);
        var pin = new ToggleSwitch { Header = "置顶", IsOn = true, HorizontalAlignment = HorizontalAlignment.Right };
        pin.Toggled += (_, _) => { if (AppWindow.Presenter is OverlappedPresenter p) p.IsAlwaysOnTop = pin.IsOn; };
        panel.Children.Add(pin);
        Content = new Grid { Background = new SolidColorBrush(Microsoft.UI.Colors.White), Children = { panel } };
        AppWindow.Resize(new Windows.Graphics.SizeInt32(850, 250));
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.IsAlwaysOnTop = true;
    }
    public string CurrentText => _line.Text;
    public void Update(string current, string next) { _line.Text = current; _next.Text = next; }
}
