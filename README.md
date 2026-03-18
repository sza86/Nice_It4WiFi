# Nice Gate

Custom Home Assistant integration for Nice IT4WIFI.

- basic control through a standard gate cover entity,
- state sensor with protocol diagnostics,
- extra T4 buttons created automatically only for commands reported by the module as allowed for the paired user.

- dodatkowy sensor diagnostyczny T4 pokazujący komendy dostępne i brakujące dla bieżącego użytkownika.


Wersja 1.5.12 dodaje diagnostykę ostatniej komendy T4: `last_command_code`, `last_command_result`, `last_change_request_xml`.
