from jev_android_mcp.toast import parse_toast


def test_only_the_selected_package_toast_is_returned() -> None:
    event = (
        "11-27 11:38:05.879 EventType: TYPE_NOTIFICATION_STATE_CHANGED; EventTime: 42; "
        "PackageName: com.example.reader; WindowId: -1; "
        "[ ClassName: android.widget.Toast; Text: [Episode B, try again]; "
        "ContentDescription: null ]"
    )
    assert parse_toast(event, "com.example.reader") == "Episode B, try again"
    assert parse_toast(event, "com.example.other") is None
    assert (
        parse_toast(
            event.replace("android.widget.Toast", "android.app.Notification"), "com.example.reader"
        )
        is None
    )
    assert (
        parse_toast(
            event.replace("TYPE_NOTIFICATION_STATE_CHANGED", "TYPE_WINDOW_STATE_CHANGED"),
            "com.example.reader",
        )
        is None
    )
