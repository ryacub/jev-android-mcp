from jev_android_mcp.state import parse_observation

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
