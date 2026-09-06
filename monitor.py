from playwright.sync_api import sync_playwright
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json
import os
import requests


# =============================================================
# SETTINGS
# =============================================================

ACCOUNTS_FILE = "accounts.json"
SEEN_FILE = "seen.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# Maximum time we allow Instagram to populate the initial grid.
PAGE_LOAD_TIMEOUT = 10

# Once the number of items stops increasing, wait this long
# before collecting the final set.
SETTLE_TIME = 1


# =============================================================
# LOAD ACCOUNTS
# =============================================================

def load_accounts():

    try:

        with open(
            ACCOUNTS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            accounts = json.load(f)


        if not isinstance(accounts, list):

            raise ValueError(
                "accounts.json must contain a JSON list."
            )


        accounts = [
            str(username).strip().lstrip("@")
            for username in accounts
            if str(username).strip()
        ]


        return accounts


    except Exception as e:

        print(
            "❌ Could not load accounts.json:",
            repr(e)
        )

        return []


# =============================================================
# LOAD SEEN
# =============================================================

def load_seen():

    if not os.path.exists(SEEN_FILE):

        print(
            "seen.json does not exist. "
            "Starting with empty database."
        )

        return {}


    try:

        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)


        if not isinstance(data, dict):

            print(
                "⚠️ seen.json is not a JSON object. "
                "Starting empty."
            )

            return {}


        return data


    except Exception as e:

        print(
            "❌ Could not read seen.json:",
            repr(e)
        )

        return {}


# =============================================================
# SAVE SEEN
# =============================================================

def save_seen(seen):

    with open(
        SEEN_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            seen,
            f,
            indent=2
        )


# =============================================================
# TELEGRAM
# =============================================================

def send_telegram(username, reel_url):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            f"[{username}] ❌ Telegram credentials missing."
        )

        return False


    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )


    message = (
        "🚨 NEW INSTAGRAM REEL\n\n"
        f"Account: @{username}\n\n"
        f"{reel_url}"
    )


    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }


    try:

        response = requests.post(
            url,
            data=data,
            timeout=20
        )


        if response.status_code != 200:

            print(
                f"[{username}] ❌ Telegram HTTP error:",
                response.status_code
            )

            print(response.text)

            return False


        try:

            result = response.json()

        except Exception:

            print(
                f"[{username}] "
                f"❌ Telegram returned invalid JSON"
            )

            print(response.text)

            return False


        if result.get("ok") is True:

            print(
                f"[{username}] ✅ Telegram sent"
            )

            return True


        print(
            f"[{username}] "
            f"❌ Telegram rejected message"
        )

        print(response.text)

        return False


    except Exception as e:

        print(
            f"[{username}] "
            f"❌ Telegram connection error:",
            repr(e)
        )

        return False


# =============================================================
# GET REELS
# =============================================================

def get_reels(page, username):

    expected_url = (
        f"https://www.instagram.com/{username}/"
    )


    current_url = (
        page.url.rstrip("/") + "/"
    )


    # Make sure Instagram actually loaded
    # the requested profile.
    if not current_url.startswith(expected_url):

        print(
            f"[{username}] ⚠️ Unexpected URL:",
            page.url
        )

        return None


    links = page.locator("a").evaluate_all(
        """elements => elements.map(e => e.href)"""
    )


    reels = {}


    for link in links:

        if not isinstance(link, str):

            continue


        if "/reel/" not in link:

            continue


        clean_url = (
            link
            .split("?")[0]
            .split("#")[0]
        )


        parts = (
            clean_url
            .rstrip("/")
            .split("/")
        )


        try:

            reel_index = parts.index("reel")

            reel_id = parts[
                reel_index + 1
            ]


        except (ValueError, IndexError):

            continue


        if not reel_id:

            continue


        if len(reel_id) < 5:

            continue


        reels[reel_id] = clean_url


    return reels


# =============================================================
# WAIT FOR GRID
# =============================================================

def wait_for_grid(page, username):

    start = time.perf_counter()

    last_count = -1
    stable_since = None


    while True:

        elapsed = (
            time.perf_counter() - start
        )


        # Get the number of profile grid links
        # currently loaded.
        count = page.locator(
            'a[href*="/p/"], a[href*="/reel/"]'
        ).count()


        # We have reached the maximum expected
        # initial grid.
        if count >= 12:

            print(
                f"[{username}] Initial grid loaded: "
                f"{count} items"
            )

            return


        # The number of items changed.
        if count != last_count:

            last_count = count

            stable_since = (
                time.perf_counter()
            )


        # We have items and the count hasn't changed
        # for SETTLE_TIME.
        elif count > 0 and stable_since is not None:

            if (
                time.perf_counter()
                - stable_since
                >= SETTLE_TIME
            ):

                print(
                    f"[{username}] Grid settled: "
                    f"{count} items"
                )

                return


        # Absolute maximum wait.
        if elapsed >= PAGE_LOAD_TIMEOUT:

            print(
                f"[{username}] Grid wait reached "
                f"{PAGE_LOAD_TIMEOUT}s: "
                f"{count} items"
            )

            return


        page.wait_for_timeout(100)


# =============================================================
# CHECK ACCOUNT
# =============================================================

def check_account(username, previous_seen):

    print(
        f"\n[{username}] Worker starting..."
    )


    # IDs successfully sent to Telegram.
    successful_ids = set()

    # IDs found during this run.
    baseline_ids = set()


    try:

        # Playwright and browser are created
        # for this account.
        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True
            )


            page = browser.new_page()


            print(
                f"[{username}] Browser started."
            )


            start_time = time.perf_counter()


            print()

            print(
                f"[{username}] CHECK"
            )


            print(
                f"[{username}] Started:",
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )


            try:

                # =================================================
                # FETCH
                # =================================================

                response = page.goto(
                    f"https://www.instagram.com/{username}/",
                    wait_until="domcontentloaded",
                    timeout=60000
                )


                print(
                    f"[{username}] HTTP status:",
                    response.status
                    if response else "Unknown"
                )


                # =================================================
                # WAIT FOR GRID
                # =================================================

                wait_for_grid(
                    page,
                    username
                )


                # =================================================
                # PROCESS
                # =================================================

                reels = get_reels(
                    page,
                    username
                )


                if reels is None:

                    print(
                        f"[{username}] "
                        f"❌ Invalid Instagram page."
                    )

                    print(
                        f"[{username}] "
                        f"Database NOT modified."
                    )


                elif len(reels) == 0:

                    print(
                        f"[{username}] "
                        f"⚠️ 0 valid reels returned."
                    )

                    print(
                        f"[{username}] "
                        f"Database NOT modified."
                    )


                else:

                    print(
                        f"[{username}] "
                        f"Reels found: {len(reels)}"
                    )


                    print(
                        f"[{username}] "
                        f"Previously seen: "
                        f"{len(previous_seen)}"
                    )


                    # =================================================
                    # INITIAL RUN FOR THIS ACCOUNT
                    # =================================================

                    if not previous_seen:

                        print(
                            f"[{username}] "
                            f"Creating baseline..."
                        )


                        baseline_ids = set(
                            reels.keys()
                        )


                        print(
                            f"[{username}] "
                            f"Baseline prepared: "
                            f"{len(baseline_ids)}"
                        )


                    # =================================================
                    # NORMAL CHECK
                    # =================================================

                    else:

                        new_reels = {

                            reel_id: reel_url

                            for reel_id, reel_url
                            in reels.items()

                            if reel_id not in previous_seen

                        }


                        if new_reels:

                            print(
                                f"[{username}] "
                                f"🚨 NEW REELS: "
                                f"{len(new_reels)}"
                            )


                            for (
                                reel_id,
                                reel_url
                            ) in new_reels.items():


                                print(
                                    f"[{username}] "
                                    f"NEW REEL ID: "
                                    f"{reel_id}"
                                )


                                print(
                                    f"[{username}] "
                                    f"URL: "
                                    f"{reel_url}"
                                )


                                # =================================================
                                # TELEGRAM
                                # =================================================

                                telegram_success = (
                                    send_telegram(
                                        username,
                                        reel_url
                                    )
                                )


                                # =================================================
                                # ONLY RETURN SUCCESSFUL IDs
                                # =================================================

                                if telegram_success:

                                    successful_ids.add(
                                        reel_id
                                    )


                                    print(
                                        f"[{username}] "
                                        f"Will save: "
                                        f"{reel_id}"
                                    )


                                else:

                                    print(
                                        f"[{username}] "
                                        f"Not saved because "
                                        f"Telegram failed."
                                    )


                        else:

                            print(
                                f"[{username}] "
                                f"No new reels."
                            )


            except Exception as e:

                print(
                    f"[{username}] "
                    f"❌ CHECK ERROR:",
                    repr(e)
                )


            # =================================================
            # CHECK FINISHED
            # =================================================

            elapsed = (
                time.perf_counter()
                - start_time
            )


            print(
                f"[{username}] "
                f"Check time: "
                f"{elapsed:.2f} seconds"
            )


            browser.close()


            print(
                f"[{username}] Browser closed."
            )


    except Exception as e:

        print(
            f"[{username}] "
            f"❌ WORKER ERROR:",
            repr(e)
        )


    return {
        "username": username,
        "baseline_ids": baseline_ids,
        "successful_ids": successful_ids
    }


# =============================================================
# MAIN
# =============================================================

print("=" * 70)

print(
    "INSTAGRAM REEL MONITOR"
)

print("=" * 70)


# =============================================================
# LOAD ACCOUNTS
# =============================================================

USERNAMES = load_accounts()


print("Accounts:")


for username in USERNAMES:

    print(
        f"  - @{username}"
    )


print()


if not USERNAMES:

    print(
        "❌ No accounts found in accounts.json."
    )

    print("=" * 70)

    print(
        "MONITOR FINISHED"
    )

    print("=" * 70)

    raise SystemExit


# =============================================================
# LOAD SEEN DATABASE ONCE
# =============================================================

seen = load_seen()


print(
    f"Seen database accounts: {len(seen)}"
)


print("=" * 70)


# =============================================================
# PREPARE ACCOUNT DATA
# =============================================================

account_seen = {}


for username in USERNAMES:

    values = seen.get(
        username,
        []
    )


    if not isinstance(values, list):

        values = []


    account_seen[username] = set(
        str(reel_id)
        for reel_id in values
    )


# =============================================================
# RUN ACCOUNTS IN PARALLEL
# =============================================================

print(
    f"Parallel workers: {len(USERNAMES)}"
)


with ThreadPoolExecutor(
    max_workers=len(USERNAMES)
) as executor:


    futures = [

        executor.submit(
            check_account,
            username,
            account_seen[username]
        )

        for username in USERNAMES

    ]


    for future in as_completed(
        futures
    ):

        try:

            result = future.result()


            username = result[
                "username"
            ]


            # =================================================
            # INITIAL BASELINE
            # =================================================

            if result["baseline_ids"]:

                seen[username] = sorted(
                    result["baseline_ids"]
                )


                print(
                    f"[{username}] "
                    f"Baseline added to database."
                )


            # =================================================
            # SUCCESSFUL TELEGRAM IDs
            # =================================================

            elif result["successful_ids"]:

                existing = set(
                    seen.get(
                        username,
                        []
                    )
                )


                existing.update(
                    result["successful_ids"]
                )


                seen[username] = sorted(
                    existing
                )


                print(
                    f"[{username}] "
                    f"Database updated with "
                    f"{len(result['successful_ids'])} "
                    f"new reel(s)."
                )


        except Exception as e:

            print(
                "Worker error:",
                repr(e)
            )


# =============================================================
# SAVE SEEN DATABASE ONCE
# =============================================================

save_seen(seen)


print()

print(
    "✅ seen.json saved."
)


print("=" * 70)

print(
    "MONITOR FINISHED"
)

print("=" * 70)
