#!/usr/bin/env bash
set -Eeuo pipefail

# Uso: ./loop.sh [plan|specs|issues|build|<N>] [max_iterações]
# Env: CODEX_TERRA_MODEL, CODEX_LUNA_MODEL, CODEX_LOG_DIR, NO_PROGRESS_LIMIT, ERROR_LIMIT

TERRA="${CODEX_TERRA_MODEL:-gpt-5.6-terra}"
LUNA="${CODEX_LUNA_MODEL:-gpt-5.6-luna}"
LOG_DIR="${CODEX_LOG_DIR:-.codex-logs}"
NO_PROGRESS_LIMIT="${NO_PROGRESS_LIMIT:-2}"
ERROR_LIMIT="${ERROR_LIMIT:-3}"

ITERATION=0; NO_PROGRESS=0; ERROR_STREAK=0
TOTAL_INPUT=0; TOTAL_CACHED=0; TOTAL_OUTPUT=0; TOTAL_REASONING=0

if [[ -t 1 ]]; then
    R=$'\033[0m' DIM=$'\033[2m' GRN=$'\033[32m' YLW=$'\033[33m' RED=$'\033[31m' CYN=$'\033[36m'
else
    R=""; DIM=""; GRN=""; YLW=""; RED=""; CYN=""
fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s%s%s\n' "$CYN" "$*" "$R"; }
ok()   { printf '%s%s%s\n' "$GRN" "$*" "$R"; }
warn() { printf '%s%s%s\n' "$YLW" "$*" "$R"; }
err()  { printf '%s%s%s\n' "$RED" "$*" "$R" >&2; }

fmt_num() {
    local n="${1//[^0-9]/}"; [[ -z "$n" ]] && n=0
    printf '%s' "$n" | rev | sed 's/\([0-9]\{3\}\)/\1./g; s/\.$//' | rev
}
fmt_dur() { printf '%dm%02ds' "$(($1/60))" "$(($1%60))"; }

case "${1:-}" in
    plan)   MODE=plan;   PROMPT=PROMPT_plan.md;   MAX="${2:-1}"; MODEL="$TERRA"; EFFORT=medium ;;
    specs)  MODE=specs;  PROMPT=PROMPT_specs.md;  MAX="${2:-1}"; MODEL="$TERRA"; EFFORT=medium ;;
    issues) MODE=issues; PROMPT=PROMPT_issues.md; MAX="${2:-0}"; MODEL="$LUNA"; EFFORT=medium ;;
    build)  MODE=build;  PROMPT=PROMPT_build.md;  MAX="${2:-0}"; MODEL="$LUNA";  EFFORT=high   ;;
    "")     MODE=build;  PROMPT=PROMPT_build.md;  MAX=0;         MODEL="$LUNA";  EFFORT=high   ;;
    [0-9]*) MODE=build;  PROMPT=PROMPT_build.md;  MAX="$1";      MODEL="$LUNA";  EFFORT=high   ;;
    *) err "Modo inválido: $1. Use: plan | specs | issues | build | <N>"; exit 1 ;;
esac

[[ -f "$PROMPT" ]]                               || { err "Arquivo não encontrado: $PROMPT"; exit 1; }
git rev-parse --is-inside-work-tree &>/dev/null  || { err "Execute dentro de um repositório Git."; exit 1; }
command -v codex &>/dev/null                     || { err "'codex' não encontrado no PATH."; exit 1; }
command -v jq    &>/dev/null                     || { err "'jq' é necessário. Instale: apt install jq"; exit 1; }

BRANCH="$(git branch --show-current)"
RUN_LOG_DIR="$LOG_DIR/${MODE}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_LOG_DIR"
START=$(date +%s)

echo "=========================================="
echo "Modo:      $MODE  |  Modelo: $MODEL  |  Esforço: $EFFORT"
echo "Branch:    $BRANCH"
echo "Logs:      $RUN_LOG_DIR/"
echo "Iterações: $( [[ "$MAX" -eq 0 ]] && echo ilimitadas || echo "$MAX" )"
echo "=========================================="

print_summary() {
    local now elapsed
    now=$(date +%s); elapsed=$((now - START))
    echo; echo "=========================================="
    ok "Resumo"
    log "Iterações : $ITERATION  |  Duração: $(fmt_dur $elapsed)"
    log "Tokens    : in=$(fmt_num $TOTAL_INPUT) cache=$(fmt_num $TOTAL_CACHED) out=$(fmt_num $TOTAL_OUTPUT) reason=$(fmt_num $TOTAL_REASONING)"
    log "Total     : $(fmt_num $((TOTAL_INPUT + TOTAL_OUTPUT + TOTAL_REASONING)))"
    log "Logs      : $RUN_LOG_DIR/"
    echo "=========================================="
}

extract_usage() {
    # Lê JSONL linha a linha, acumula tokens de todos os eventos turn.completed
    local file="$1" i=0 c=0 o=0 r=0 line usage
    while IFS= read -r line; do
        type=$(printf '%s' "$line" | jq -r '.type // ""' 2>/dev/null) || continue
        [[ "$type" == "turn.completed" ]] || continue
        usage=$(printf '%s' "$line" | jq -r '
            .usage // {} |
            "\(.input_tokens // 0) \(.cached_input_tokens // .cache_read_input_tokens // 0) \(.output_tokens // 0) \(.reasoning_output_tokens // .reasoning_tokens // 0)"
        ' 2>/dev/null) || continue
        read -r li lc lo lr <<< "$usage"
        i=$((i + ${li//[^0-9]/0}))
        c=$((c + ${lc//[^0-9]/0}))
        o=$((o + ${lo//[^0-9]/0}))
        r=$((r + ${lr//[^0-9]/0}))
    done < "$file"
    echo "$i $c $o $r"
}

render_stream() {
    jq -r '
        if   .type == "thread.started" then "\u001b[2m[thread] \(.thread_id // "?")\u001b[0m"
        elif .type == "item.completed" then "\u001b[35m[\(.item.type // "item")]\u001b[0m \(.item.text // .item.command // .item.aggregated_output // (.item | tostring))"
        elif .type == "turn.completed" then "\u001b[2m[turn concluída]\u001b[0m"
        elif .type == "error"          then "\u001b[31m[erro] \(.message // .error // (. | tostring))\u001b[0m"
        else empty end
    ' 2>/dev/null || true
}

trap 'warn "\nInterrompido."; print_summary; exit 130' INT TERM

while true; do
    [[ "$MAX" -gt 0 && "$ITERATION" -ge "$MAX" ]] && { info "Limite atingido: $MAX"; break; }

    echo; info "--- Iteração $((ITERATION+1)) ---"

    BEFORE="$(git status --porcelain)"
    ITER_LOG="$RUN_LOG_DIR/iter-$((ITERATION+1)).jsonl"
    ITER_START=$(date +%s)

    set +e
    codex exec --dangerously-bypass-approvals-and-sandbox \
        --model "$MODEL" --config "model_reasoning_effort=\"$EFFORT\"" \
        --ephemeral --json - < "$PROMPT" \
        | tee "$ITER_LOG" | render_stream
    EXIT=${PIPESTATUS[0]}
    set -e

    ITERATION=$((ITERATION+1))
    ITER_END=$(date +%s)
    AFTER="$(git status --porcelain)"

    if [[ "$EXIT" -ne 0 ]]; then
        ERROR_STREAK=$((ERROR_STREAK+1))
        err "codex falhou (exit $EXIT) — streak: $ERROR_STREAK/$ERROR_LIMIT"
        [[ "$ERROR_STREAK" -ge "$ERROR_LIMIT" ]] && { err "Parando por excesso de falhas."; print_summary; exit 1; }
        continue
    fi
    ERROR_STREAK=0

    read -r I C O RS <<< "$(extract_usage "$ITER_LOG")"
    TOTAL_INPUT=$((TOTAL_INPUT+I)); TOTAL_CACHED=$((TOTAL_CACHED+C))
    TOTAL_OUTPUT=$((TOTAL_OUTPUT+O)); TOTAL_REASONING=$((TOTAL_REASONING+RS))

    echo
    log "$(fmt_dur $((ITER_END-ITER_START)))  |  ${DIM}in=$(fmt_num $I) cache=$(fmt_num $C) out=$(fmt_num $O) reason=$(fmt_num $RS)${R}"

    LABEL="$(jq -r 'select(.type=="item.completed") | .item.text // ""' "$ITER_LOG" 2>/dev/null \
        | grep -Eo 'BUILD_COMPLETED|BUILD_BLOCKED|BUILD_COMPLETE|ISSUES_COMPLETE|ISSUES_BLOCKED|ISSUE_CREATED' \
        | tail -1 || true)"

    [[ -n "$LABEL" ]] && info "Label: $LABEL"

    case "$LABEL" in
        ISSUES_COMPLETE|BUILD_COMPLETE)  ok "Concluído: $LABEL"; break ;;
        ISSUES_BLOCKED|BUILD_BLOCKED)   warn "Bloqueado: $LABEL"; break ;;
        ISSUE_CREATED) NO_PROGRESS=0; ok "Iteração $ITERATION OK (ISSUE_CREATED)."; continue ;;
    esac

    if [[ "$MODE" == "build" ]]; then
        if [[ "$BEFORE" == "$AFTER" ]]; then
            NO_PROGRESS=$((NO_PROGRESS+1))
            warn "Sem progresso no git: $NO_PROGRESS/$NO_PROGRESS_LIMIT"
            [[ "$NO_PROGRESS" -ge "$NO_PROGRESS_LIMIT" ]] && { warn "Parando."; break; }
        else
            NO_PROGRESS=0
            git diff --stat 2>/dev/null | tail -n 5 || true
        fi
        if [[ "$MAX" -eq 0 && -d issues ]]; then
            OPEN=$(grep -rl '^status:[[:space:]]*open[[:space:]]*$' issues/ --include='*.md' 2>/dev/null | grep -v '0000' | wc -l | tr -d '[:space:]')
            OPEN=${OPEN:-0}
            ok "Iteração $ITERATION OK. Issues abertas: $OPEN"
            [[ "$OPEN" -eq 0 ]] && { ok "Todas as issues encerradas."; break; }
        else
            ok "Iteração $ITERATION OK."
        fi
    else
        NO_PROGRESS=$((NO_PROGRESS+1))
        warn "Sem label reconhecido: $NO_PROGRESS/$NO_PROGRESS_LIMIT"
        [[ "$NO_PROGRESS" -ge "$NO_PROGRESS_LIMIT" ]] && { warn "Parando."; break; }
        ok "Iteração $ITERATION OK."
    fi
done

print_summary