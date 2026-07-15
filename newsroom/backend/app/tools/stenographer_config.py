"""Rule configuration for the deterministic interview stenographer.

The numeric cut-offs in this module are deliberately kept out of the scoring
code so the report can point to one auditable scoring rubric.
"""

# Source for every rubric threshold below: the course teacher's project brief,
# as reproduced in the 2026-07-15 stenographer task.  No threshold is inferred
# from a model or presented as a published research result.
RUBRIC_SOURCE = "课程教师项目任务书（2026-07-15 stenographer 任务）"

CLOSED_PREFIXES = ("是不是", "有没有", "对不对", "能不能", "会不会")
CLOSED_ENDINGS = ("吗", "吧")
CLOSED_MAX_LENGTH_EXCLUSIVE = 15

OPEN_MARKERS = ("怎么", "为什么", "如何", "什么样", "能说说", "描述一下")

# A follow-up needs at least two shared content words (noun/verb) with the
# previous guest answer.  Source: the course teacher's project brief above.
FOLLOW_UP_MIN_SHARED_WORDS = 2

# Questions longer than 50 characters are counted as long.  Source: the course
# teacher's project brief above.
LONG_QUESTION_LENGTH_EXCLUSIVE = 50

# Two question marks, or any transition below, make one host turn a multi-part
# question.  Source: the course teacher's project brief above.
MULTI_QUESTION_MARK_MINIMUM = 2
MULTI_QUESTION_MARKERS = ("另外", "还有就是", "顺便问一下")

LEADING_QUESTION_PATTERNS = (
    r"您是不是觉得",
    r"难道不是",
    r"大家都认为.*您",
    r"说白了就是",
)

FILLERS = ("这个", "那个", "然后", "就是说", "对吧", "呃", "嗯")
FILLER_TOP_LIMIT = 5

# Ideal ranges used by the report.  Source: the course teacher's project brief
# above; these are course assessment targets, not fabricated literature claims.
IDEAL_HOST_TALK_RATIO_MAX_EXCLUSIVE = 0.3
IDEAL_OPEN_RATIO_MIN_EXCLUSIVE = 0.6

METRIC_DECIMAL_PLACES = 4

# Function words and interview scaffolding are removed before noun/verb overlap
# is measured.  This list is configuration rather than a learned resource, so
# the same transcript always receives the same score.
CONTENT_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "和",
        "与",
        "及",
        "或",
        "而",
        "但",
        "也",
        "都",
        "就",
        "还",
        "又",
        "很",
        "更",
        "最",
        "被",
        "把",
        "让",
        "给",
        "在",
        "到",
        "从",
        "对",
        "向",
        "为",
        "以",
        "于",
        "是",
        "有",
        "能",
        "会",
        "要",
        "可以",
        "可能",
        "应该",
        "觉得",
        "认为",
        "说",
        "讲",
        "问",
        "请",
        "您",
        "你",
        "我",
        "他",
        "她",
        "它",
        "我们",
        "你们",
        "他们",
        "这",
        "那",
        "这个",
        "那个",
        "什么",
        "怎么",
        "为什么",
        "如何",
        "一下",
        "吗",
        "吧",
        "呢",
        "啊",
        "哦",
        "嗯",
        "呃",
    }
)
