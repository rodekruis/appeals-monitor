from unittest.mock import Mock, patch

from scripts.deploy_kobo_form import publish_form


def _response(*, status_code: int, text: str = "") -> Mock:
    response = Mock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 400
    response.text = text
    return response


@patch("scripts.deploy_kobo_form.requests.patch")
@patch("scripts.deploy_kobo_form.requests.get")
def test_publish_form_reports_deployment_response_body(
    mock_get: Mock, mock_patch: Mock
) -> None:
    asset = Mock()
    asset.raise_for_status.return_value = None
    asset.json.return_value = {"version_id": "v-latest"}
    mock_get.return_value = asset
    mock_patch.return_value = _response(
        status_code=400, text="draft must be deployed first"
    )

    try:
        publish_form("https://kobo.example", "token", "asset-uid")
    except RuntimeError as exc:
        assert str(exc) == (
            "asset-uid: deployment API returned HTTP 400: draft must be deployed first"
        )
    else:
        raise AssertionError("publish_form should report a failed deployment")
