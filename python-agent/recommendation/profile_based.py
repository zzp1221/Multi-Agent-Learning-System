"""
Profile-based recommendation engine for learning resources.

Scores resources against a user profile and applies greedy diversity
re-ranking to produce a balanced recommendation list.

Usage as CLI:  python profile_based.py
"""

import json
import sys
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from src.ai_modules.config import get_settings

# ── Constants ──────────────────────────────────────────────────

DB_CONFIG = get_settings().postgres_connect_kwargs()

DIFFICULTY_ORDER = ["BASIC", "INTERMEDIATE", "ADVANCED"]

LEARNING_STYLE_TYPE_MAP = {
    "theory":   frozenset(["READING", "MINDMAP", "SLIDES", "DOCUMENT", "PPT"]),
    "practice": frozenset(["PRACTICE", "QUIZ", "CODE"]),
    "visual":   frozenset(["VIDEO", "MINDMAP", "IMAGE"]),
}


# ── User Profile ───────────────────────────────────────────────

# ── Helper: Jaccard coefficient ────────────────────────────────

def jaccard(set_a: frozenset, set_b: frozenset) -> float:
    """Jaccard similarity coefficient between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ── Difficulty adjacency ───────────────────────────────────────

def _difficulty_score(resource_diff: str, preferred: list[str]) -> float:
    """Score a resource's difficulty level against preferred difficulties.

    Returns:
        20 for exact match, 10 for adjacent or MIXED, 0 otherwise.
    """
    if not resource_diff or not preferred:
        return 0.0
    resource_diff = resource_diff.upper()
    preferred_upper = [d.upper() for d in preferred]

    # Exact match
    if resource_diff in preferred_upper:
        return 20.0

    # MIXED covers multiple levels — treat as adjacent to any level
    if resource_diff == "MIXED":
        return 10.0

    # Adjacent match
    if resource_diff in DIFFICULTY_ORDER:
        idx = DIFFICULTY_ORDER.index(resource_diff)
        for pref in preferred_upper:
            if pref in DIFFICULTY_ORDER:
                pref_idx = DIFFICULTY_ORDER.index(pref)
                if abs(idx - pref_idx) == 1:
                    return 10.0

    return 0.0


# ── Course extraction ──────────────────────────────────────────

def _extract_course(resource: dict) -> Optional[str]:
    """Extract the most likely course name from a resource row.

    Checks metadata_json.course first, then title prefix before '-' or '（'.
    """
    # 1) metadata_json.course
    meta = resource.get("metadata_json")
    if isinstance(meta, dict):
        course = meta.get("course")
        if course and isinstance(course, str) and course.strip():
            return course.strip()

    # 2) Title prefix heuristic — many titles are "操作系统-..." or "数据结构..."
    title = resource.get("title", "")
    if title:
        for sep in ("-", "（", "("):
            prefix = title.split(sep, 1)[0].strip()
            if prefix and len(prefix) >= 2:
                return prefix

    return None


# ── Learning style bonus ───────────────────────────────────────

def _learning_style_score(resource_type: str, learning_style: str) -> float:
    """Return score bonus when resource_type aligns with learning style."""
    if learning_style == "balanced":
        return 10.0  # small bonus for everything
    mapped = LEARNING_STYLE_TYPE_MAP.get(learning_style)
    if mapped and resource_type in mapped:
        return 15.0
    return 0.0


# ── Recommender ────────────────────────────────────────────────

class ProfileBasedRecommender:
    """Profile-based learning resource recommender with greedy diversity."""

    def __init__(self, db_config: dict):
        self.db_config = db_config

    # ── fetch ──────────────────────────────────────────────────

    def _fetch_resources(self) -> list[dict]:
        """Query all ACTIVE COMPUTER_SCIENCE resources from app.learning_resource."""
        conn = psycopg2.connect(**self.db_config)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, title, resource_type, difficulty_level,
                           tags, summary_text, metadata_json
                    FROM app.learning_resource
                    WHERE domain = 'COMPUTER_SCIENCE'
                      AND status = 'ACTIVE'
                    ORDER BY title
                """)
                rows = cur.fetchall()
                # Convert RealDictRow to plain dict for easier mutation
                resources = []
                for row in rows:
                    r = dict(row)
                    # Ensure tags is a list
                    tags = r.get("tags")
                    if isinstance(tags, str):
                        try:
                            r["tags"] = json.loads(tags)
                        except (json.JSONDecodeError, TypeError):
                            r["tags"] = []
                    if not isinstance(r.get("tags"), list):
                        r["tags"] = []
                    # Ensure metadata_json is a dict
                    meta = r.get("metadata_json")
                    if isinstance(meta, str):
                        try:
                            r["metadata_json"] = json.loads(meta)
                        except (json.JSONDecodeError, TypeError):
                            r["metadata_json"] = {}
                    if not isinstance(r.get("metadata_json"), dict):
                        r["metadata_json"] = {}
                    resources.append(r)
                return resources
        finally:
            conn.close()

    # ── score ──────────────────────────────────────────────────

    def _score_resource(self, resource: dict, profile: dict) -> dict:
        """Score a single resource against the profile.

        Returns a dict with 'score' (float) and 'reason_parts' (list[str]).
        """
        score = 0.0
        reasons: list[str] = []

        # Unpack profile
        preferred_courses = profile.get("preferred_courses", []) or []
        preferred_difficulty = profile.get("preferred_difficulty", []) or []
        interested_tags = profile.get("interested_tags", []) or []
        preferred_types = profile.get("preferred_types", []) or []
        learning_style = profile.get("learning_style", "balanced") or "balanced"

        resource_type = resource.get("resource_type", "")
        resource_diff = resource.get("difficulty_level", "")
        resource_tags: list[str] = resource.get("tags", []) or []

        # 1) Course match: +30
        course = _extract_course(resource)
        if course and preferred_courses:
            for pc in preferred_courses:
                if pc and pc.strip() == course:
                    score += 30
                    reasons.append(f"Course: {course}")
                    break

        # 2) Difficulty match
        diff_score = _difficulty_score(resource_diff, preferred_difficulty)
        if diff_score > 0:
            score += diff_score
            label = "exact" if diff_score >= 20 else "adjacent"
            reasons.append(f"Difficulty {label} ({resource_diff})")

        # 3) Tag overlap: Jaccard * 25
        if interested_tags and resource_tags:
            j = jaccard(frozenset(interested_tags), frozenset(resource_tags))
            tag_score = j * 25
            if tag_score > 0:
                score += tag_score
                shared = [t for t in interested_tags if t in resource_tags]
                reasons.append(f"Tags: {', '.join(shared[:4])}")

        # 4) Type bonus: +15
        if resource_type and preferred_types:
            if resource_type in preferred_types:
                score += 15
                reasons.append(f"Type: {resource_type}")

        # 5) Learning style bonus
        style_score = _learning_style_score(resource_type, learning_style)
        if style_score > 0:
            score += style_score
            if learning_style != "balanced":
                reasons.append(f"Style: {learning_style}")

        return {"score": score, "reason_parts": reasons}

    # ── recommend ──────────────────────────────────────────────

    def recommend(self, profile: dict, top_n: int = 10,
                  exclude_seen: bool = True) -> list[dict]:
        """Generate top_n diverse recommendations for a user profile.

        Args:
            profile: dict with keys: preferred_courses,
                     preferred_difficulty, interested_tags, completed_resource_ids,
                     preferred_types, learning_style.
            top_n: Number of recommendations to return.
            exclude_seen: If True, skip resources whose id is in
                          completed_resource_ids.

        Returns:
            List of dicts with keys: id, title, resource_type,
            difficulty_level, score, reason.
        """
        # Fetch
        resources = self._fetch_resources()
        if not resources:
            return []

        # Exclude completed
        if exclude_seen:
            seen = set(profile.get("completed_resource_ids", []) or [])
            resources = [r for r in resources if r["id"] not in seen]

        if not resources:
            return []

        # Score every resource
        scored: list[dict] = []
        for r in resources:
            result = self._score_resource(r, profile)
            r["_score"] = result["score"]
            r["_reason_parts"] = result["reason_parts"]
            scored.append(r)

        # Greedy diversity selection
        scored.sort(key=lambda x: x["_score"], reverse=True)
        selected: list[dict] = []

        while scored and len(selected) < top_n:
            # Pick the best remaining
            best = scored.pop(0)
            selected.append(best)

            # Penalize remaining resources of the same type
            for r in scored:
                if r["resource_type"] == best["resource_type"]:
                    r["_score"] -= 5.0

            # Re-sort
            scored.sort(key=lambda x: x["_score"], reverse=True)

        # Build output
        output = []
        for r in selected:
            reason = "; ".join(r.get("_reason_parts", [])) or "General recommendation"
            output.append({
                "id": str(r["id"]),
                "title": r["title"],
                "resource_type": r["resource_type"],
                "difficulty_level": r["difficulty_level"],
                "score": round(r["_score"], 1),
                "reason": reason,
            })

        return output


# ── Student profile builder ────────────────────────────────────

def build_student_profile(
    courses: Optional[list[str]] = None,
    difficulty: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    types: Optional[list[str]] = None,
    learning_style: str = "balanced",
    completed_ids: Optional[list[str]] = None,
) -> dict:
    """Build a reasonable default profile for a CS student.

    Args:
        courses: Preferred courses.  Defaults to a broad CS curriculum.
        difficulty: Preferred difficulty levels.  Defaults to INTERMEDIATE.
        tags: Interested topic tags.  Defaults to common CS topics.
        types: Preferred resource types.  Defaults to a diverse mix.
        learning_style: One of theory/practice/visual/balanced.
        completed_ids: Already-completed resource IDs.

    Returns:
        A dict suitable for ProfileBasedRecommender.recommend().
    """
    return {
        "preferred_courses": courses or [
            "操作系统", "数据结构", "计算机网络", "计算机组成原理",
            "编译原理", "数据库", "算法", "软件工程", "离散数学",
            "程序设计", "Python", "Rust", "Git", "Linux", "Redis",
            "设计模式", "SQL",
        ],
        "preferred_difficulty": difficulty or ["INTERMEDIATE"],
        "interested_tags": tags or [
            "操作系统", "数据结构", "算法", "计算机网络", "数据库",
            "编译原理", "设计模式", "Python", "图论", "字符串匹配",
            "动态规划", "TCP", "HTTP", "内存管理", "红黑树",
        ],
        "preferred_types": types or [
            "VIDEO", "READING", "MINDMAP", "QUIZ",
        ],
        "learning_style": learning_style,
        "completed_resource_ids": completed_ids or [],
    }


# ── CLI test mode ──────────────────────────────────────────────

def main():
    """Run a test recommendation for the default student profile."""
    print("=" * 70)
    print("Profile-Based Recommendation Engine — Test Run")
    print("=" * 70)

    recommender = ProfileBasedRecommender(DB_CONFIG)

    profile = build_student_profile()
    print("\n[Profile]")
    print(f"  Courses:   {', '.join(profile['preferred_courses'][:5])}...")
    print(f"  Difficulty: {profile['preferred_difficulty']}")
    print(f"  Tags:      {', '.join(profile['interested_tags'][:5])}...")
    print(f"  Types:     {profile['preferred_types']}")
    print(f"  Style:     {profile['learning_style']}")

    print("\n[Fetching & Scoring resources...]")
    recommendations = recommender.recommend(profile, top_n=10)

    if not recommendations:
        print("\nNo recommendations found.  Check that resources exist")
        print("in app.learning_resource with domain='COMPUTER_SCIENCE' and status='ACTIVE'.")
        return

    print(f"\nTop {len(recommendations)} Recommendations:\n")
    print(f"  {'#':<3} {'Score':<7} {'Type':<12} {'Difficulty':<14} {'Title'}")
    print("  " + "-" * 65)
    for i, rec in enumerate(recommendations, 1):
        title_short = rec["title"][:50]
        print(f"  {i:<3} {rec['score']:<7.1f} {rec['resource_type']:<12} "
              f"{rec['difficulty_level']:<14} {title_short}")
        print(f"      Reason: {rec['reason']}")

    print("\nDone.")


if __name__ == "__main__":
    main()
