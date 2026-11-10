"""Stream names shared by all services. Commands start work; events report what happened."""

# Commands (published by the API / scheduler)
CMD_DISCOVER = "cmd.discover"            # {"platforms": [..] | null}
CMD_APPLY = "cmd.apply"                  # {"platforms": [..] | null, "dry_run": bool, "limit": int | null}
CMD_TRACK = "cmd.track"                  # {}
CMD_FIND_STARTUPS = "cmd.find_startups"  # {"cities": ["bangalore", "hyderabad"]}

# Events
JOB_DISCOVERED = "evt.job.discovered"        # JobPosting fields
JOB_NEW = "evt.job.new"                      # full job row (with "id"), only for jobs not seen before
JOB_SCORED = "evt.job.scored"                # {"id", "score", "reasons": [..], "passed": bool}
JOB_DESCRIPTION = "evt.job.description"      # {"id", "description"}
JOB_APPLY_RESULT = "evt.job.apply_result"    # {"id", "status", "note"}
EMAIL_RECEIVED = "evt.email.received"        # {"message_id", "subject", "sender", "body", "received"}
COMPANY_CHECKED = "evt.company.checked"      # company record from the startup finder

COMMANDS = {
    "discover": CMD_DISCOVER,
    "apply": CMD_APPLY,
    "track": CMD_TRACK,
    "find_startups": CMD_FIND_STARTUPS,
}
