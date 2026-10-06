"""LMS service layer exceptions."""


class LMSError(Exception):
    """Base LMS error."""


class LMSNotFoundError(LMSError):
    """Resource not found."""


class LMSValidationError(LMSError):
    """Invalid input or business rule violation."""


class UnansweredQuestionsError(LMSValidationError):
    """A student tried to submit a quiz / diagnostic with questions still unanswered."""

    def __init__(self, question_numbers):
        self.question_numbers = list(question_numbers)
        count = len(self.question_numbers)
        shown = ", ".join(str(n) for n in self.question_numbers[:12]) + ("…" if count > 12 else "")
        super().__init__(
            f"Please answer every question before you submit. "
            f"{count} question{'' if count == 1 else 's'} still unanswered: {shown}."
        )


class LMSPermissionError(LMSError):
    """Caller lacks permission for the operation."""
