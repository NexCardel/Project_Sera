"""
alert_service.py
----------------
Action-to-Alert Mapping and Safe Identity Formatter for Sera Alert System.
Ensures zero sensitive credential leaks and consistent level color mapping.
"""

from typing import Optional, Tuple

class ActionAlertFormatter:
    """
    Action-to-alert matrix mapper according to Sera Alert System Blueprint.
    """

    MATRIX = {
        # Action: (Template with {client_name}, Generic Fallback, Level)
        "autofill": ("Auto-filled {client_name}", "Auto-filled credentials", "success"),
        "manual_copy": ("Manually copied {client_name}", "Credentials copied to clipboard", "success"),
        "manual_assist": ("Manual assist opened for {client_name}", "Manual assist opened", "info"),
        "create": ("Client created: {client_name}", "Client created successfully", "success"),
        "update": ("Client updated: {client_name}", "Client updated successfully", "info"),
        "archive": ("Client archived: {client_name}", "Client archived", "warning"),
        "unarchive": ("Client restored: {client_name}", "Client restored successfully", "success"),
        "delete": ("Client deleted: {client_name}", "Client deleted", "error"),
        "csv_import": ("Clients imported successfully", "Clients imported successfully", "success"),
        "backup": ("Backup completed successfully", "Backup completed successfully", "info"),
        "restore": ("Database restored successfully", "Database restored successfully", "success"),
        "csv_export": ("CSV export completed", "CSV export completed", "error"),  # Red per security policy
    }

    @classmethod
    def format(cls, action: str, client_name: Optional[str] = None) -> Optional[Tuple[str, str]]:
        """
        Formats an audit action into (user_facing_message, level).
        Returns None for actions that do not produce alerts (e.g. 'view').
        """
        action = action.lower().strip()
        if action == "view" or action not in cls.MATRIX:
            return None

        template, fallback, level = cls.MATRIX[action]
        
        if client_name and client_name.strip():
            # Truncate long client names safely to avoid overflow
            clean_name = client_name.strip()
            if len(clean_name) > 30:
                clean_name = clean_name[:27] + "..."
            message = template.format(client_name=clean_name)
        else:
            message = fallback

        return message, level
