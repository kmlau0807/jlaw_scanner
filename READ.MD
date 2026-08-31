run.bat email-test          # quick SMTP check
run.bat scan                # print the 10+10 tables
run.bat scan --email        # scan + email the report
run.bat schedule            # daily APScheduler loop


Command	What it does
run.bat scan	Fastest self-test — scans both HK + US, prints the top-10 BUY/SELL table to the console. No email sent. Use this to verify the engine.
run.bat email-test	Sends one test email to confirm Gmail SMTP creds in config.ini work. Run this before your first real email scan.
run.bat scan --email	Full scan + sends the report email (charts embedded).
run.bat refresh-hk	Manually rebuild universe/hk.csv from live HSI+HSCEI + your manual overrides (normally the daily job does this).
run.bat refresh-us	Same, for the US universe.
run.bat schedule	Legacy foreground scheduler (blocks until Ctrl+C). The real 09:00 trigger is the WorkBuddy automation, so you rarely need this.