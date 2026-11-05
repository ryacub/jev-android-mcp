from jev_android_mcp.models import Bounds
from jev_android_mcp.state import parse_observation, pinned_window

XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example.reader" clickable="false" enabled="true"
        focusable="false" focused="false" scrollable="false"
        checkable="false" checked="false" selected="false" password="false"
        bounds="[0,0][1080,1920]">
    <node index="0" text="Search" resource-id="app:id/search" class="android.widget.Button"
          package="com.example.reader" clickable="true" enabled="true"
          focusable="true" focused="false" scrollable="false"
          checkable="false" checked="false" selected="false" password="false"
          bounds="[10,10][300,100]" />
    <node index="1" text="" resource-id="" class="android.widget.EditText"
          package="com.example.reader" clickable="true" enabled="true"
          focusable="true" focused="true" scrollable="false"
          checkable="false" checked="false" selected="false" password="false"
          bounds="[10,120][800,220]" />
    <node index="2" text="" resource-id="" class="android.widget.ScrollView"
          package="com.example.reader" clickable="false" enabled="true"
          focusable="false" focused="false" scrollable="true"
          checkable="false" checked="false" selected="false" password="false"
          bounds="[0,220][1080,1920]" />
  </node>
</hierarchy>
"""


def test_parse_observation_builds_compact_action_space() -> None:
    observation = parse_observation(XML, "emulator-5554")

    assert observation.package == "com.example.reader"
    assert observation.rotation == 0
    assert observation.observation_id
    assert len(observation.nodes) == 5
    assert {action.operation for action in observation.actions} == {
        "TAP",
        "TYPE_TEXT",
        "SCROLL",
        "PRESS_ENTER",
        "BACK",
        "HOME",
        "ROTATE_PORTRAIT",
        "ROTATE_LANDSCAPE",
        "WAIT",
        "DONE",
        "BLOCKED",
    }
    assert any(
        action.label == "Search" and action.operation == "TAP" for action in observation.actions
    )
    assert all(
        action.observation_id == observation.observation_id for action in observation.actions
    )
    assert all(
        action.id.startswith(f"{observation.observation_id}:") for action in observation.actions
    )
    assert any(action.operation == "TYPE_TEXT" for action in observation.actions)
    assert observation.fingerprint


def test_fingerprint_changes_when_visible_state_changes() -> None:
    changed = XML.replace('text="Search"', 'text="Library"')

    first = parse_observation(XML, "emulator-5554")
    second = parse_observation(changed, "emulator-5554")

    assert first.fingerprint != second.fingerprint


def test_action_uses_descendant_text_for_compose_container() -> None:
    child = (
        '<node index="0" text="Library" resource-id="" class="android.widget.TextView" '
        'package="com.example.reader" clickable="false" enabled="true" focusable="false" '
        'focused="false" scrollable="false" checkable="false" checked="false" '
        'selected="false" password="false" bounds="[20,20][200,90]" />'
    )
    xml = XML.replace(
        '<node index="0" text="Search" resource-id="app:id/search" class="android.widget.Button"',
        '<node index="0" text="" resource-id="" class="android.view.View"',
    ).replace(
        'bounds="[10,10][300,100]" />',
        f'bounds="[10,10][300,100]">{child}</node>',
        1,
    )

    observation = parse_observation(xml, "emulator-5554")

    assert any(
        action.operation == "TAP" and action.label == "Library" for action in observation.actions
    )


def test_disabled_and_zero_area_nodes_are_not_actions() -> None:
    xml = XML.replace('clickable="true" enabled="true"', 'clickable="true" enabled="false"')
    xml = xml.replace('bounds="[10,120][800,220]"', 'bounds="[10,120][10,220]"')

    observation = parse_observation(xml, "emulator-5554")

    assert not any(
        action.operation == "TAP" and action.label == "Search" for action in observation.actions
    )
    assert not any(action.operation == "TYPE_TEXT" for action in observation.actions)


def test_password_text_is_redacted_before_observation() -> None:
    xml = XML.replace('text="Search"', 'text="secret-value"').replace(
        'checked="false" selected="false" password="false"\n          bounds="[10,10][300,100]"',
        'checked="false" selected="false" password="true"\n          bounds="[10,10][300,100]"',
    )

    observation = parse_observation(xml, "emulator-5554")

    assert all(node.text != "secret-value" for node in observation.nodes)
    assert all("secret-value" not in action.label for action in observation.actions)


def test_password_content_description_is_redacted_before_observation() -> None:
    xml = XML.replace('content-desc=""', 'content-desc="secret-value"', 1).replace(
        'checked="false" selected="false" password="false"\n          bounds="[10,10][300,100]"',
        'checked="false" selected="false" password="true"\n          bounds="[10,10][300,100]"',
    )

    observation = parse_observation(xml, "emulator-5554")

    assert all(node.content_description != "secret-value" for node in observation.nodes)
    assert all("secret-value" not in action.label for action in observation.actions)


def test_video_surface_gets_a_tap_action_without_clickable() -> None:
    xml = XML.replace(
        '<node index="2" text="" resource-id="" class="android.widget.ScrollView"',
        '<node index="3" text="" resource-id="app:id/player_surface"'
        ' class="android.view.SurfaceView"'
        ' package="com.example.reader" clickable="false" enabled="true"'
        ' focusable="false" focused="false" scrollable="false"'
        ' checkable="false" checked="false" selected="false" password="false"'
        ' bounds="[0,0][2400,1080]" />\n'
        '    <node index="2" text="" resource-id="" class="android.widget.ScrollView"',
    )

    observation = parse_observation(xml, "emulator-5554")

    surface_taps = [
        action
        for action in observation.actions
        if action.operation == "TAP" and "Video surface" in action.label
    ]
    assert len(surface_taps) == 1
    assert "player_surface" in surface_taps[0].label


def test_zero_area_video_surface_is_not_an_action() -> None:
    xml = XML.replace(
        '<node index="2" text="" resource-id="" class="android.widget.ScrollView"',
        '<node index="3" text="" resource-id="" class="android.view.TextureView"'
        ' package="com.example.reader" clickable="false" enabled="true"'
        ' focusable="false" focused="false" scrollable="false"'
        ' checkable="false" checked="false" selected="false" password="false"'
        ' bounds="[0,0][0,0]" />\n'
        '    <node index="2" text="" resource-id="" class="android.widget.ScrollView"',
    )

    observation = parse_observation(xml, "emulator-5554")

    assert not any("Video surface" in action.label for action in observation.actions)


PINNED_DUMPSYS = "\n".join(
    [
        "ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)",
        "  * Task{65f465f #80 type=standard A=10225:com.example.reader U=0"
        " visible=true mode=pinned translucent=false sz=1}",
        "    mLastPausedActivity: ActivityRecord{1 u0 com.example.reader/.PlayerActivity t80}",
        "        mOverrideConfig={1.0 winConfig={ mBounds=Rect(440, 1959 - 1038, 2295)"
        " mAppBounds=Rect(440, 1959 - 1038, 2295) mWindowingMode=pinned}}",
        "  * Task{c04fa30 #79 type=standard A=10225:com.example.reader U=0"
        " visible=true mode=fullscreen translucent=false sz=1}",
        "        mOverrideConfig={1.0 winConfig={ mBounds=Rect(0, 0 - 1080, 2400)"
        " mWindowingMode=fullscreen}}",
    ]
)


def test_pinned_window_reads_package_and_bounds() -> None:
    assert pinned_window(PINNED_DUMPSYS) == (
        "com.example.reader",
        Bounds(left=440, top=1959, right=1038, bottom=2295),
    )


def test_pinned_window_is_none_without_a_pinned_task() -> None:
    assert pinned_window(PINNED_DUMPSYS.replace("mode=pinned", "mode=fullscreen")) is None


def test_pinned_window_gets_a_tap_action() -> None:
    pinned = ("com.example.reader", Bounds(left=440, top=1959, right=1038, bottom=2295))

    observation = parse_observation(XML, "emulator-5554", pinned=pinned)

    taps = [
        action
        for action in observation.actions
        if action.operation == "TAP" and "Picture-in-picture window" in action.label
    ]
    assert len(taps) == 1
    node = next(node for node in observation.nodes if node.id == taps[0].node_id)
    assert node.bounds == pinned[1]
    assert observation.package == parse_observation(XML, "emulator-5554").package
