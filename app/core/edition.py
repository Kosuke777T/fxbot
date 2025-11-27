from dataclasses import dataclass

# -------------------------------
# EditionGuard: エディション別の機能フラグを集中管理
# -------------------------------

@dataclass(frozen=True)
class EditionCapability:
    demo_only: bool
    lot_limit: float | None
    scheduler_jobs_max: int
    diagnosis_level: str            # "none" / "basic" / "full"
    ranking_send: bool
    filter_level: str               # "none" / "simple" / "full"
    shap_limit: int | None          # None = unlimited
    fi_limit: int | None
    profile_multi: bool
    profile_auto_switch: bool


class EditionGuard:

    def __init__(self, edition: str):
        edition = edition.lower()
        valid = ["free", "basic", "pro", "expert", "master"]
        if edition not in valid:
            raise ValueError(f"Unknown edition: {edition}")
        self.edition = edition

    # -------------------------------
    #  各エディションの能力セット
    # -------------------------------
    def get_capability(self) -> EditionCapability:
        e = self.edition

        if e == "free":
            return EditionCapability(
                demo_only=True,
                lot_limit=0.03,
                scheduler_jobs_max=0,
                diagnosis_level="none",
                ranking_send=False,
                filter_level="none",
                shap_limit=0,
                fi_limit=0,
                profile_multi=False,
                profile_auto_switch=False,
            )

        if e == "basic":
            return EditionCapability(
                demo_only=False,
                lot_limit=0.1,
                scheduler_jobs_max=1,        # fixed weekly job only
                diagnosis_level="none",
                ranking_send=True,
                filter_level="none",
                shap_limit=0,
                fi_limit=0,
                profile_multi=False,
                profile_auto_switch=False,
            )

        if e == "pro":
            return EditionCapability(
                demo_only=False,
                lot_limit=None,
                scheduler_jobs_max=1,         # retrain or diagnose のどちらか1つ
                diagnosis_level="basic",      # v0
                ranking_send=True,
                filter_level="simple",
                shap_limit=3,                 # SHAP Top3
                fi_limit=20,                  # FI Top20
                profile_multi=True,
                profile_auto_switch=False,
            )

        if e == "expert":
            return EditionCapability(
                demo_only=False,
                lot_limit=None,
                scheduler_jobs_max=5,
                diagnosis_level="full",
                ranking_send=True,
                filter_level="full",
                shap_limit=20,
                fi_limit=None,                # unlimited
                profile_multi=True,
                profile_auto_switch=True,
            )

        # master
        return EditionCapability(
            demo_only=False,
            lot_limit=None,
            scheduler_jobs_max=99,
            diagnosis_level="full",
            ranking_send=True,
            filter_level="full",
            shap_limit=None,
            fi_limit=None,
            profile_multi=True,
            profile_auto_switch=True,
        )


# -------------------------------
# テスト実行用
# -------------------------------
def _debug():
    for e in ["free", "basic", "pro", "expert", "master"]:
        g = EditionGuard(e)
        c = g.get_capability()
        print(f"=== {e.upper()} ===")
        print(c)


if __name__ == "__main__":
    _debug()