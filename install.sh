#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly DEFAULT_INSTALL_DIR="/opt/x2bsky"
readonly DEFAULT_RUNTIME_DIR="/var/lib/x2bsky"
readonly DEFAULT_CONFIG_DIR="/etc/x2bsky"
readonly DEFAULT_UNIT_DIR="/etc/systemd/system"
readonly DEFAULT_SERVICE_USER="x2bsky"

TEST_ROOT=""
CHECK_ONLY=false
VENV_BUILD=""
APP_BUILD=""
CHECK_RUNTIME=""

cleanup_staging() {
    if [[ -n "${INSTALL_DIR:-}" \
        && ! -d "$INSTALL_DIR/.venv" \
        && -d "$INSTALL_DIR/.venv-previous" ]]; then
        mv -- "$INSTALL_DIR/.venv-previous" "$INSTALL_DIR/.venv"
    fi
    if [[ -n "$VENV_BUILD" && -d "$VENV_BUILD" ]]; then
        rm -rf -- "$VENV_BUILD"
    fi
    if [[ -n "$APP_BUILD" && -d "$APP_BUILD" ]]; then
        rm -rf -- "$APP_BUILD"
    fi
    if [[ -n "$CHECK_RUNTIME" && -d "$CHECK_RUNTIME" ]]; then
        rm -rf -- "$CHECK_RUNTIME"
    fi
}

report_unexpected_error() {
    local status=$?
    printf 'FEHLER: Installation unerwartet abgebrochen (Zeile %s, Exitcode %s).\n' \
        "${BASH_LINENO[0]:-unbekannt}" "$status" >&2
    printf 'Die genaue Ursache steht unmittelbar über dieser Meldung. Ein erneuter Start ist sicher.\n' >&2
    return "$status"
}

trap cleanup_staging EXIT
trap report_unexpected_error ERR

usage() {
    printf '%s\n' \
        "Verwendung: $0 [--check] [--test-root VERZEICHNIS]" \
        "" \
        "Ohne Optionen: vollständige interaktive Systeminstallation." \
        "--check: Quelldateien und Vorlagen prüfen, nichts schreiben." \
        "--test-root: isoliert installieren, ohne root/systemd/pip."
}

fail() {
    printf 'FEHLER: %s\n' "$*" >&2
    exit 1
}

require_source_files() {
    local relative
    for relative in \
        app/x2bsky.py \
        app/x2bsky_common.py \
        README.md \
        INSTALLATION.md \
        LICENSE \
        docs/ARCHITECTURE.md \
        docs/Installationsanleitung.docx \
        requirements.txt \
        systemd/x2bsky.service.in \
        systemd/x2bsky.timer; do
        [[ -f "$SCRIPT_DIR/$relative" ]] || fail "Quelldatei fehlt: $relative"
    done
}

require_commands() {
    local command_name
    for command_name in \
        flock install python3 runuser sed systemctl systemd-analyze useradd; do
        command -v "$command_name" >/dev/null 2>&1 \
            || fail "Erforderliches Systemprogramm fehlt: $command_name"
    done
    [[ -x /usr/sbin/nologin ]] || fail "Erforderliche Shell fehlt: /usr/sbin/nologin"
    python3 -c 'import ensurepip, ssl, sys, venv; raise SystemExit(sys.version_info < (3, 10))' \
        || fail "Python 3.10 oder neuer wird benötigt"
}

install_missing_prerequisites() {
    [[ -r /etc/os-release ]] || fail "Betriebssystem kann nicht erkannt werden"
    # shellcheck disable=SC1091
    source /etc/os-release
    case "${ID:-}:${VERSION_ID:-}" in
        debian:12*|ubuntu:22.04|ubuntu:24.04|linuxmint:21*|linuxmint:22*) ;;
        *)
            fail "Unterstützt werden Debian 12, Ubuntu 22.04/24.04 und Linux Mint 21/22; erkannt: ${ID:-unbekannt} ${VERSION_ID:-}"
            ;;
    esac
    if command -v python3 >/dev/null 2>&1 \
        && command -v flock >/dev/null 2>&1 \
        && command -v runuser >/dev/null 2>&1 \
        && python3 -c 'import ensurepip, ssl, sys, venv; raise SystemExit(sys.version_info < (3, 10))' \
            >/dev/null 2>&1; then
        return
    fi
    command -v apt-get >/dev/null 2>&1 \
        || fail "apt-get fehlt; Voraussetzungen können nicht installiert werden"
    printf 'Installiere fehlende Systemvoraussetzungen …\n'
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        coreutils passwd python3 python3-pip python3-venv sed systemd util-linux
}

validate_sources() {
    command -v python3 >/dev/null 2>&1 || fail "Python 3 fehlt"
    python3 -c \
        'import pathlib, sys; [compile(pathlib.Path(p).read_bytes(), p, "exec") for p in sys.argv[1:]]' \
        "$SCRIPT_DIR/app/x2bsky.py" "$SCRIPT_DIR/app/x2bsky_common.py"
    python3 -c \
        'import sys, zipfile; raise SystemExit(not zipfile.is_zipfile(sys.argv[1]))' \
        "$SCRIPT_DIR/docs/Installationsanleitung.docx"
}

validate_username() {
    [[ "$1" =~ ^[A-Za-z0-9_]{1,15}$ ]] || fail "X-Benutzername ist ungültig"
}

validate_handle() {
    [[ "$1" =~ ^[A-Za-z0-9._:-]+$ ]] || fail "Bluesky-Handle ist ungültig"
}

validate_domains() {
    [[ -z "$1" || "$1" =~ ^[A-Za-z0-9.-]+(,[A-Za-z0-9.-]+)*$ ]] \
        || fail "Domains müssen kommaseparierte Hostnamen sein"
}

validate_url() {
    [[ "$1" =~ ^https://[A-Za-z0-9._~:/?@%+,=-]+$ ]] \
        || fail "Fallback-URL enthält ungültige Zeichen"
}

prompt_if_empty() {
    local variable_name="$1"
    local prompt="$2"
    local secret="${3:-false}"
    local value="${!variable_name:-}"
    if [[ -z "$value" ]]; then
        if [[ "$secret" == true ]]; then
            read -r -s -p "$prompt: " value \
                || fail "Eingabe abgebrochen: $prompt"
            printf '\n'
        else
            read -r -p "$prompt: " value \
                || fail "Eingabe abgebrochen: $prompt"
        fi
        printf -v "$variable_name" '%s' "$value"
    fi
}

confirm_configuration() {
    printf '\nBitte prüfen:\n'
    printf '  X-Konto:          @%s\n' "$X2BSKY_X_USERNAME"
    printf '  Bluesky-Konto:    %s\n' "$X2BSKY_BLUESKY_HANDLE"
    printf '  Fallback-URL:     %s\n' "$X2BSKY_FALLBACK_URL"
    printf '  Bevorzugte Links: %s\n' "${X2BSKY_MY_DOMAINS:-keine}"
    printf '  App-Passwort:     verdeckt und nicht ausgegeben\n\n'
    case "${X2BSKY_CONFIRM:-}" in
        yes|YES|ja|JA|1) return ;;
    esac
    [[ -t 0 ]] \
        || fail "Keine Bestätigung möglich; unbeaufsichtigt X2BSKY_CONFIRM=yes setzen"
    local answer
    read -r -p "Sind diese Angaben richtig? [j/N]: " answer \
        || fail "Bestätigung abgebrochen"
    [[ "$answer" =~ ^[jJyY]([aAeEsS])?$ ]] || fail "Installation auf Wunsch abgebrochen"
}

escape_systemd_value() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    printf '"%s"' "$value"
}

render_service() {
    local template="$1"
    local target="$2"
    local install_dir="$3"
    local runtime_dir="$4"
    local config_file="$5"
    local service_user="$6"
    sed \
        -e "s|@@INSTALL_DIR@@|$install_dir|g" \
        -e "s|@@RUNTIME_DIR@@|$runtime_dir|g" \
        -e "s|@@CONFIG_FILE@@|$config_file|g" \
        -e "s|@@SERVICE_USER@@|$service_user|g" \
        "$template" >"$target"
    chmod 0644 "$target"
}

while (($#)); do
    case "$1" in
        --check)
            CHECK_ONLY=true
            shift
            ;;
        --test-root)
            (($# >= 2)) || fail "--test-root benötigt ein Verzeichnis"
            TEST_ROOT="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Unbekannte Option: $1"
            ;;
    esac
done

require_source_files
bash -n "$SCRIPT_DIR/install.sh"

if [[ "$CHECK_ONLY" == true ]]; then
    validate_sources
    printf 'Quell- und Vorlagenprüfung erfolgreich.\n'
    exit 0
fi

if [[ -z "$TEST_ROOT" && ${EUID} -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || fail "Erforderliches Systemprogramm fehlt: sudo"
    exec sudo "$0"
fi

X2BSKY_X_USERNAME="${X2BSKY_X_USERNAME:-}"
X2BSKY_BLUESKY_HANDLE="${X2BSKY_BLUESKY_HANDLE:-}"
X2BSKY_BLUESKY_APP_PASSWORD="${X2BSKY_BLUESKY_APP_PASSWORD:-}"
X2BSKY_MY_DOMAINS="${X2BSKY_MY_DOMAINS:-}"
X2BSKY_FALLBACK_URL="${X2BSKY_FALLBACK_URL:-}"

printf '%s\n' \
    "" \
    "x2bsky-Einrichtung" \
    "-------------------" \
    "Benötigt werden ein öffentliches X-Konto und ein Bluesky-App-Passwort." \
    "Das App-Passwort wird in Bluesky unter Einstellungen > Datenschutz und Sicherheit > App-Passwörter erzeugt." \
    "Die Fallback-URL ist die HTTPS-Seite, die verwendet wird, wenn ein X-Post keinen externen Link enthält." \
    "Bevorzugte Domains sind optional und priorisieren passende Links aus einem X-Post." \
    "Es wird während der Installation nichts veröffentlicht."

prompt_if_empty X2BSKY_X_USERNAME "X-Benutzername ohne @"
prompt_if_empty X2BSKY_BLUESKY_HANDLE "Bluesky-Handle"
prompt_if_empty X2BSKY_BLUESKY_APP_PASSWORD "Bluesky-App-Passwort" true
prompt_if_empty X2BSKY_FALLBACK_URL "Fallback-URL einschließlich https://"
if [[ -z "$X2BSKY_MY_DOMAINS" ]]; then
    read -r -p "Bevorzugte Domains, kommasepariert (optional): " X2BSKY_MY_DOMAINS \
        || fail "Eingabe abgebrochen: bevorzugte Domains"
fi

validate_username "$X2BSKY_X_USERNAME"
validate_handle "$X2BSKY_BLUESKY_HANDLE"
validate_domains "$X2BSKY_MY_DOMAINS"
validate_url "$X2BSKY_FALLBACK_URL"
[[ -n "$X2BSKY_BLUESKY_APP_PASSWORD" ]] || fail "Bluesky-App-Passwort darf nicht leer sein"
confirm_configuration

if [[ -z "$TEST_ROOT" ]]; then
    install_missing_prerequisites
    require_commands
    [[ -d /run/systemd/system ]] || fail "systemd ist auf diesem System nicht aktiv"
fi
validate_sources

if [[ -n "$TEST_ROOT" ]]; then
    [[ "$TEST_ROOT" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        || fail "--test-root muss ein einfacher absoluter Pfad sein"
    INSTALL_DIR="$TEST_ROOT/opt/x2bsky"
    RUNTIME_DIR="$TEST_ROOT/var/lib/x2bsky"
    CONFIG_DIR="$TEST_ROOT/etc/x2bsky"
    UNIT_DIR="$TEST_ROOT/etc/systemd/system"
    SERVICE_USER="$(id -un)"
else
    INSTALL_DIR="$DEFAULT_INSTALL_DIR"
    RUNTIME_DIR="$DEFAULT_RUNTIME_DIR"
    CONFIG_DIR="$DEFAULT_CONFIG_DIR"
    UNIT_DIR="$DEFAULT_UNIT_DIR"
    SERVICE_USER="$DEFAULT_SERVICE_USER"
fi

if [[ -e "$INSTALL_DIR" && ! -f "$INSTALL_DIR/.managed-by-x2bsky-installer" ]]; then
    fail "Installationsziel existiert und wird nicht von x2bsky verwaltet: $INSTALL_DIR"
fi
if [[ -e "$RUNTIME_DIR" && ! -f "$RUNTIME_DIR/.managed-by-x2bsky-installer" ]]; then
    fail "Laufzeitverzeichnis existiert und wird nicht von x2bsky verwaltet: $RUNTIME_DIR"
fi
if [[ -e "$CONFIG_DIR" && ! -f "$CONFIG_DIR/.managed-by-x2bsky-installer" ]]; then
    fail "Konfigurationsverzeichnis existiert und wird nicht von x2bsky verwaltet: $CONFIG_DIR"
fi
if [[ -e "$UNIT_DIR/x2bsky.service" ]] \
    && ! grep -q '^# Managed by x2bsky installer$' "$UNIT_DIR/x2bsky.service"; then
    fail "Vorhandener x2bsky.service stammt nicht von diesem Installer"
fi

if [[ -z "$TEST_ROOT" ]] && ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$RUNTIME_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -m 0755 "$INSTALL_DIR/app" "$INSTALL_DIR/docs" "$UNIT_DIR"
install -d -m 0750 "$RUNTIME_DIR" "$RUNTIME_DIR/logs" "$RUNTIME_DIR/state" "$RUNTIME_DIR/tmp"
install -d -m 0700 "$RUNTIME_DIR/credentials"
install -d -m 0750 "$CONFIG_DIR"
printf 'managed\n' >"$INSTALL_DIR/.managed-by-x2bsky-installer"
printf 'managed\n' >"$RUNTIME_DIR/.managed-by-x2bsky-installer"
printf 'managed\n' >"$CONFIG_DIR/.managed-by-x2bsky-installer"
chmod 0644 "$INSTALL_DIR/.managed-by-x2bsky-installer"
chmod 0640 "$RUNTIME_DIR/.managed-by-x2bsky-installer"
chmod 0640 "$CONFIG_DIR/.managed-by-x2bsky-installer"

exec 9>"$RUNTIME_DIR/state/run.lock"
flock --exclusive --timeout 60 9 \
    || fail "x2bsky läuft noch; Installation nach Ende des Laufs erneut starten"

if [[ -z "$TEST_ROOT" ]]; then
    printf 'Erstelle und prüfe die neue Python-Umgebung …\n'
    VENV_BUILD="$INSTALL_DIR/.venv-build"
    python3 -m venv --clear "$VENV_BUILD"
    "$VENV_BUILD/bin/pip" install --upgrade pip
    "$VENV_BUILD/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
    "$VENV_BUILD/bin/pip" check

    APP_BUILD="$INSTALL_DIR/.app-build"
    install -d -m 0755 "$APP_BUILD"
    install -m 0755 "$SCRIPT_DIR/app/x2bsky.py" "$APP_BUILD/x2bsky.py"
    install -m 0644 "$SCRIPT_DIR/app/x2bsky_common.py" "$APP_BUILD/x2bsky_common.py"

    CHECK_RUNTIME="$RUNTIME_DIR/.install-check"
    install -d -m 0700 "$CHECK_RUNTIME/credentials"
    install -d -m 0750 "$CHECK_RUNTIME/logs" "$CHECK_RUNTIME/state" "$CHECK_RUNTIME/tmp"
    printf '%s\n%s\n' "$X2BSKY_BLUESKY_HANDLE" "$X2BSKY_BLUESKY_APP_PASSWORD" \
        >"$CHECK_RUNTIME/credentials/bluesky.credentials"
    chmod 0600 "$CHECK_RUNTIME/credentials/bluesky.credentials"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$RUNTIME_DIR"

    printf 'Prüfe Bluesky-Anmeldung, ohne etwas zu veröffentlichen …\n'
    runuser -u "$SERVICE_USER" -- \
        env \
            X2BSKY_RUNTIME_DIR="$CHECK_RUNTIME" \
            X2BSKY_X_USERNAME="$X2BSKY_X_USERNAME" \
            X2BSKY_MY_DOMAINS="$X2BSKY_MY_DOMAINS" \
            X2BSKY_FALLBACK_URL="$X2BSKY_FALLBACK_URL" \
            "$VENV_BUILD/bin/python" "$APP_BUILD/x2bsky.py" --check-login
    printf 'Prüfe das öffentliche X-Profil, ohne etwas zu veröffentlichen …\n'
    runuser -u "$SERVICE_USER" -- \
        env \
            X2BSKY_RUNTIME_DIR="$CHECK_RUNTIME" \
            X2BSKY_X_USERNAME="$X2BSKY_X_USERNAME" \
            X2BSKY_MY_DOMAINS="$X2BSKY_MY_DOMAINS" \
            X2BSKY_FALLBACK_URL="$X2BSKY_FALLBACK_URL" \
            X2BSKY_DRY_RUN=1 \
            "$VENV_BUILD/bin/python" "$APP_BUILD/x2bsky.py" --dry-run
    rm -rf -- "$CHECK_RUNTIME"
    CHECK_RUNTIME=""
fi
if [[ -n "$APP_BUILD" ]]; then
    install -m 0755 "$APP_BUILD/x2bsky.py" "$INSTALL_DIR/app/x2bsky.py"
    install -m 0644 "$APP_BUILD/x2bsky_common.py" "$INSTALL_DIR/app/x2bsky_common.py"
    rm -rf -- "$APP_BUILD"
    APP_BUILD=""
else
    install -m 0755 "$SCRIPT_DIR/app/x2bsky.py" "$INSTALL_DIR/app/x2bsky.py"
    install -m 0644 "$SCRIPT_DIR/app/x2bsky_common.py" "$INSTALL_DIR/app/x2bsky_common.py"
fi
install -m 0644 "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
for document in README.md INSTALLATION.md LICENSE docs/ARCHITECTURE.md docs/Installationsanleitung.docx; do
    install -m 0644 "$SCRIPT_DIR/$document" "$INSTALL_DIR/$document"
done

CONFIG_FILE="$CONFIG_DIR/x2bsky.env"
{
    printf 'X2BSKY_RUNTIME_DIR=%s\n' "$(escape_systemd_value "$RUNTIME_DIR")"
    printf 'X2BSKY_X_USERNAME=%s\n' "$(escape_systemd_value "$X2BSKY_X_USERNAME")"
    printf 'X2BSKY_MY_DOMAINS=%s\n' "$(escape_systemd_value "$X2BSKY_MY_DOMAINS")"
    printf 'X2BSKY_FALLBACK_URL=%s\n' "$(escape_systemd_value "$X2BSKY_FALLBACK_URL")"
    printf 'X2BSKY_INCLUDE_RETWEETS="1"\n'
    printf 'X2BSKY_INCLUDE_REPLIES="0"\n'
    printf 'X2BSKY_INCLUDE_POLLS="0"\n'
    printf 'X2BSKY_MAX_PER_RUN="6"\n'
} >"$CONFIG_FILE"
chmod 0640 "$CONFIG_FILE"

CREDENTIAL_FILE="$RUNTIME_DIR/credentials/bluesky.credentials"
printf '%s\n%s\n' "$X2BSKY_BLUESKY_HANDLE" "$X2BSKY_BLUESKY_APP_PASSWORD" >"$CREDENTIAL_FILE"
chmod 0600 "$CREDENTIAL_FILE"

render_service \
    "$SCRIPT_DIR/systemd/x2bsky.service.in" \
    "$UNIT_DIR/x2bsky.service" \
    "$INSTALL_DIR" \
    "$RUNTIME_DIR" \
    "$CONFIG_FILE" \
    "$SERVICE_USER"
install -m 0644 "$SCRIPT_DIR/systemd/x2bsky.timer" "$UNIT_DIR/x2bsky.timer"

if [[ -n "$TEST_ROOT" ]]; then
    printf 'Isolierte Testinstallation erfolgreich: %s\n' "$TEST_ROOT"
    exit 0
fi

chown -R root:root "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$RUNTIME_DIR"
chown root:"$SERVICE_USER" "$CONFIG_FILE"
if [[ -d "$INSTALL_DIR/.venv-previous" ]]; then
    rm -rf -- "$INSTALL_DIR/.venv-previous"
fi
if [[ -d "$INSTALL_DIR/.venv" ]]; then
    mv -- "$INSTALL_DIR/.venv" "$INSTALL_DIR/.venv-previous"
fi
mv -- "$VENV_BUILD" "$INSTALL_DIR/.venv"
VENV_BUILD=""
rm -rf -- "$INSTALL_DIR/.venv-previous"
systemd-analyze verify "$UNIT_DIR/x2bsky.service" "$UNIT_DIR/x2bsky.timer"
systemctl daemon-reload
flock --unlock 9

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a
runuser -u "$SERVICE_USER" -- \
    env \
        X2BSKY_RUNTIME_DIR="$X2BSKY_RUNTIME_DIR" \
        X2BSKY_X_USERNAME="$X2BSKY_X_USERNAME" \
        X2BSKY_MY_DOMAINS="$X2BSKY_MY_DOMAINS" \
        X2BSKY_FALLBACK_URL="$X2BSKY_FALLBACK_URL" \
        "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/app/x2bsky.py" --check-login
runuser -u "$SERVICE_USER" -- \
    env \
        X2BSKY_RUNTIME_DIR="$X2BSKY_RUNTIME_DIR" \
        X2BSKY_X_USERNAME="$X2BSKY_X_USERNAME" \
        X2BSKY_MY_DOMAINS="$X2BSKY_MY_DOMAINS" \
        X2BSKY_FALLBACK_URL="$X2BSKY_FALLBACK_URL" \
        X2BSKY_DRY_RUN=1 \
        "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/app/x2bsky.py" --dry-run

systemctl enable --now x2bsky.timer
printf '\nInstallation vollständig erfolgreich.\n'
printf 'x2bsky veröffentlicht ab jetzt stündlich geeignete neue Posts.\n'
printf 'Nächster Lauf:\n'
systemctl list-timers x2bsky.timer --no-pager
