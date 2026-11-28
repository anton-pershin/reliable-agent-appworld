from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from reliable_agent_appworld.contracts import Task, TaskState
from reliable_agent_appworld.protocol import appworld_discovery_action

SUPERVISOR_EMAIL = "supervisor@example.test"
SUPERVISOR_PHONE = "+1-555-010-0000"
SUPPORTED_APPS = (
    "amazon",
    "file_system",
    "gmail",
    "phone",
    "simple_note",
    "splitwise",
    "spotify",
    "todoist",
    "venmo",
)
PRIMARY_APIS = {
    "amazon": "search_products",
    "file_system": "search_files",
    "gmail": "search_emails",
    "phone": "search_contacts",
    "simple_note": "search_notes",
    "splitwise": "search_expenses",
    "spotify": "search_songs",
    "todoist": "search_tasks",
    "venmo": "search_users",
}
CONTACTS = [
    {
        "contact_id": 17,
        "first_name": "Riley",
        "last_name": "Roommate",
        "email": "riley@example.test",
        "phone_number": "+1-555-010-2222",
        "relationships": ["roommate"],
        "private_note": "RAW-CONTACT-PRIVATE-NOTE",
    }
]
FAILED_CONTACTS = {
    "status": "error",
    "message": "RAW-FAILED-CONTACT-PAYLOAD",
    "debug": {
        "password": "phone-login-password-SECRET",
        "access_token": "phone-access-token-SECRET",
    },
}


def password_for(app_name: str) -> str:
    return f"{app_name}-login-password-SECRET"


def token_for(app_name: str) -> str:
    return f"{app_name}-access-token-SECRET"


def failed_login_response(app_name: str) -> dict:
    return {
        "status": "error",
        "message": f"RAW-{app_name}-LOGIN-RESPONSE",
        "debug": {
            "password": password_for(app_name),
            "session": f"RAW-{app_name}-LOGIN-SECRET",
        },
    }


def api_doc(app_name: str, api_name: str) -> dict:
    parameters = []
    success_response: object = {"results": "array"}
    if api_name == "login":
        parameters = [
            {
                "name": "username",
                "type": "string",
                "required": True,
                "description": "Account email or phone number.",
                "constraints": [],
            },
            {
                "name": "password",
                "type": "string",
                "required": True,
                "description": "Account password.",
                "constraints": [],
            },
        ]
        success_response = {"access_token": "string"}
    elif api_name == "search_contacts":
        parameters = [
            {
                "name": "access_token",
                "type": "string",
                "required": True,
                "description": "Phone session token.",
                "constraints": [],
            },
            {
                "name": "query",
                "type": "string",
                "required": False,
                "description": "Optional contact-name query.",
                "constraints": [],
            },
        ]
        success_response = {"contacts": "array"}
    elif api_name == "show_profile":
        success_response = {
            "first_name": "string",
            "last_name": "string",
            "email": "string",
            "phone_number": "string",
        }
    elif api_name == "show_account_passwords":
        success_response = {"accounts": "array"}
    return {
        "app_name": app_name,
        "api_name": api_name,
        "description": f"Public schema for {app_name}.{api_name}.",
        "parameters": parameters,
        "response_schemas": {"success": success_response},
    }


class FakeApiDocs:
    def __init__(self, app_names: tuple[str, ...]):
        self.app_names = app_names
        self.search_calls = []
        self.show_calls = []

    def search_api_docs(self, *, query, page_index, page_limit):
        self.search_calls.append(
            {"query": query, "page_index": page_index, "page_limit": page_limit}
        )
        # Search can return fuzzy, unrelated matches; objective aliases must narrow them.
        return [api_doc(app_name, PRIMARY_APIS[app_name]) for app_name in self.app_names]

    def show_api_doc(self, *, app_name, api_name):
        self.show_calls.append({"app_name": app_name, "api_name": api_name})
        return api_doc(app_name, api_name)


class FakeSupervisor:
    def show_profile(self):
        return {
            "first_name": "Sam",
            "last_name": "Supervisor",
            "email": SUPERVISOR_EMAIL,
            "phone_number": SUPERVISOR_PHONE,
        }

    def show_account_passwords(self):
        return [
            {"account_name": app_name, "password": password_for(app_name)}
            for app_name in SUPPORTED_APPS
        ]


class FakeApp:
    def __init__(self, app_name: str, owner: FakePublicAPIs):
        self.app_name = app_name
        self.owner = owner

    def login(self, *, username, password):
        self.owner.login_calls.append(
            {
                "app_name": self.app_name,
                "username": username,
                "password": password,
            }
        )
        if self.app_name in self.owner.failed_logins:
            return failed_login_response(self.app_name)
        return {"access_token": token_for(self.app_name)}


class FakePhone(FakeApp):
    def search_contacts(self, *, access_token, page_index, page_limit):
        self.owner.contact_calls.append(
            {
                "access_token": access_token,
                "page_index": page_index,
                "page_limit": page_limit,
            }
        )
        return self.owner.contacts_response


class FakePublicAPIs:
    def __init__(
        self,
        docs_app_names=(
            "phone",
            "venmo",
            "spotify",
        ),
        *,
        failed_logins=(),
        contacts_response=None,
    ):
        self.failed_logins = frozenset(failed_logins)
        self.contacts_response = CONTACTS if contacts_response is None else contacts_response
        self.login_calls = []
        self.contact_calls = []
        self.api_docs = FakeApiDocs(tuple(docs_app_names))
        self.supervisor = FakeSupervisor()
        for app_name in SUPPORTED_APPS:
            app_type = FakePhone if app_name == "phone" else FakeApp
            setattr(self, app_name, app_type(app_name, self))


def public_state(objective: str) -> TaskState:
    return TaskState(
        task=Task(
            id="onboarding-execution",
            instructions="Use execute_code.",
            input=json.dumps(
                {
                    "instruction": objective,
                    "supervisor": {
                        "first_name": "Sam",
                        "last_name": "Supervisor",
                        "email": SUPERVISOR_EMAIL,
                        "phone_number": SUPERVISOR_PHONE,
                    },
                    "app_descriptions": {
                        "phone": "Contacts and calls.",
                        "spotify": "Music and podcasts.",
                        "venmo": "Payments and social transactions.",
                    },
                }
            ),
        )
    )


def execute_onboarding(
    objective: str,
    apis: FakePublicAPIs,
    *,
    authenticate: bool = True,
    private_only: bool = False,
):
    decision = appworld_discovery_action(
        public_state(objective), authenticate=authenticate, private_only=private_only
    )
    assert decision is not None
    code = decision.action.arguments["code"]
    namespace = {"apis": apis}
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exec(compile(code, "<appworld-onboarding>", "exec"), namespace)
    return namespace, stdout.getvalue()


def assert_no_private_output(output: str) -> None:
    for app_name in SUPPORTED_APPS:
        assert password_for(app_name) not in output
        assert token_for(app_name) not in output


def test_relevant_app_alias_filter_avoids_unrelated_logins():
    apis = FakePublicAPIs(docs_app_names=("file_system", "gmail", "spotify"))

    namespace, output = execute_onboarding("Save my note in the file system.", apis)

    assert [call["app_name"] for call in apis.login_calls] == ["file_system"]
    assert namespace["access_tokens"] == {"file_system": token_for("file_system")}
    login_doc_apps = [
        call["app_name"]
        for call in apis.api_docs.show_calls
        if call["api_name"] == "login"
    ]
    assert login_doc_apps == ["file_system"]
    assert_no_private_output(output)


def test_objective_aliases_cover_apps_missing_from_search_results():
    apis = FakePublicAPIs(docs_app_names=("spotify",))

    namespace, output = execute_onboarding(
        "Read my SimpleNote and send the payment with Venmo.", apis
    )

    assert [call["app_name"] for call in apis.login_calls] == ["simple_note", "venmo"]
    assert namespace["access_tokens"] == {
        "simple_note": token_for("simple_note"),
        "venmo": token_for("venmo"),
    }
    assert_no_private_output(output)


def test_domain_aliases_cover_implicit_apps_without_search_support():
    cases = [
        ("Create a playlist from my SimpleNote songs.", {"simple_note", "spotify"}),
        ("Set my wake-up alarm.", {"phone"}),
        ("Reorganize files under ~/documents.", {"file_system"}),
        ("Send the owed money and reply by phone text.", {"phone", "venmo"}),
        ("Read my messages.", {"phone"}),
        ("Organize my music collection.", {"spotify"}),
        ("Reorganize files under /home/user/documents.", {"file_system"}),
        ("Read documents/personal/notes.md.", {"file_system"}),
    ]
    for objective, expected_apps in cases:
        apis = FakePublicAPIs(docs_app_names=())
        namespace, output = execute_onboarding(objective, apis)
        assert {call["app_name"] for call in apis.login_calls} == expected_apps
        assert set(namespace["access_tokens"]) == expected_apps
        assert_no_private_output(output)


def test_phone_uses_phone_number_other_apps_use_email_and_tokens_stay_per_app():
    apis = FakePublicAPIs(docs_app_names=("gmail", "phone", "venmo"))

    namespace, output = execute_onboarding(
        "Use phone, Venmo, and Gmail for calls, payments, and email.", apis
    )

    logins = {call["app_name"]: call for call in apis.login_calls}
    assert set(logins) == {"gmail", "phone", "venmo"}
    assert logins["phone"]["username"] == SUPERVISOR_PHONE
    assert logins["gmail"]["username"] == SUPERVISOR_EMAIL
    assert logins["venmo"]["username"] == SUPERVISOR_EMAIL
    for app_name, call in logins.items():
        assert call["password"] == password_for(app_name)
    assert namespace["access_tokens"] == {
        "gmail": token_for("gmail"),
        "phone": token_for("phone"),
        "venmo": token_for("venmo"),
    }
    assert_no_private_output(output)


def test_failed_login_prints_fixed_status_without_raw_response_or_secrets():
    apis = FakePublicAPIs(docs_app_names=("venmo",), failed_logins=("venmo",))

    namespace, output = execute_onboarding("Check Venmo.", apis)

    assert namespace["access_tokens"] == {}
    failure_statuses = [
        line for line in output.splitlines() if line.startswith("Login failed for venmo")
    ]
    assert len(failure_statuses) == 1
    raw_response = failed_login_response("venmo")
    assert repr(raw_response) not in output
    assert raw_response["message"] not in output
    assert raw_response["debug"]["session"] not in output
    assert_no_private_output(output)


def test_relationship_task_authenticates_phone_and_prefetches_compact_contacts():
    apis = FakePublicAPIs(docs_app_names=("venmo",))

    namespace, output = execute_onboarding(
        "Find my roommate's contact details so I can pay them with Venmo.", apis
    )

    assert {call["app_name"] for call in apis.login_calls} == {"phone", "venmo"}
    assert namespace["access_tokens"]["phone"] == token_for("phone")
    assert apis.contact_calls == [
        {
            "access_token": token_for("phone"),
            "page_index": 0,
            "page_limit": 20,
        }
    ]
    assert "Riley" in output
    assert "Roommate" in output
    assert "riley@example.test" in output
    assert "+1-555-010-2222" in output
    assert "roommate" in output
    assert "contact_id" not in output
    assert "RAW-CONTACT-PRIVATE-NOTE" not in output
    assert_no_private_output(output)


def test_failed_contact_prefetch_never_prints_raw_payload_or_secrets():
    apis = FakePublicAPIs(
        docs_app_names=("phone",), contacts_response=dict(FAILED_CONTACTS)
    )

    _, output = execute_onboarding("Find my friend's phone number.", apis)

    assert apis.contact_calls[0]["access_token"] == token_for("phone")
    assert repr(FAILED_CONTACTS) not in output
    assert FAILED_CONTACTS["message"] not in output
    assert "RAW-FAILED-CONTACT-PAYLOAD" not in output
    assert_no_private_output(output)


def test_adaptive_onboarding_skips_public_answer_but_runs_action_only_objective():
    answer_apis = FakePublicAPIs(docs_app_names=("spotify",))
    answer_decision = appworld_discovery_action(
        public_state("Who is the most followed artist on Spotify?"),
        authenticate=True,
        private_only=True,
    )

    assert answer_decision is None
    assert answer_apis.login_calls == []
    assert answer_apis.api_docs.search_calls == []

    action_apis = FakePublicAPIs(docs_app_names=("spotify",))
    namespace, output = execute_onboarding(
        "Play the newest public song on Spotify.", action_apis, private_only=True
    )

    assert [call["app_name"] for call in action_apis.login_calls] == ["spotify"]
    assert namespace["access_tokens"] == {"spotify": token_for("spotify")}
    assert_no_private_output(output)
