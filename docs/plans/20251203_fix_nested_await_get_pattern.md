# Plan: Fix Nested `await Get()` Pattern in Clojure Backend

## Status: Phases 1-2 Complete

## Implementation Results

### Phase 1: ✅ Complete
- Fixed `aot_compile.py:162-163`
- `pants check` passes

### Phase 2: ✅ Complete
- Fixed `check.py:189-190`
- `pants check` passes

### Phase 3: Tested - Hang Persists
- The `test_hang_repro.py` test still hangs after these changes
- This confirms the nested pattern was not the root cause of the hang
- However, fixing the anti-pattern is still the right thing to do

### Phase 4-5: Skipped
- Since the hang persists, the root cause is elsewhere
- The test_hang_repro.py file documents an ongoing issue

## Conclusion

The nested `await Get()` anti-pattern has been fixed in both files. While this did not resolve the hang issue in `test_hang_repro.py`, the fix is still valuable because:
1. It aligns with Pants best practices (zero nested patterns in Pants codebase)
2. It makes the code more readable
3. It matches the working pattern in `test.py`

The hang issue requires further investigation - the root cause is not the nested pattern.

---

## Original Plan (for reference)

## Problem Summary

The Clojure backend uses a nested `await Get()` pattern that is an anti-pattern in Pants rules. This pattern may cause scheduler issues, particularly in RuleRunner tests.

**Current problematic pattern** (in `aot_compile.py:162` and `check.py:189`):
```python
process_result = await Get(FallibleProcessResult, Process, await Get(Process, JvmProcess, process))
```

**Correct sequential pattern** (already used in `test.py:199-202`):
```python
process = await Get(Process, JvmProcess, jvm_process)
result = await Get(FallibleProcessResult, Process, process)
```

## Background

Investigation confirmed:
1. There are **zero instances** of nested `await Get()` patterns in the entire Pants codebase
2. The working `test.py` in the Clojure backend already uses the correct sequential pattern
3. The nested pattern may cause scheduler deadlocks in certain scenarios

## Files Modified

| File | Line | Change |
|------|------|--------|
| `pants-plugins/clojure_backend/aot_compile.py` | 162-163 | Split nested `await Get()` into sequential calls |
| `pants-plugins/clojure_backend/goals/check.py` | 189-190 | Split nested `await Get()` into sequential calls |

## Changes Made

### Phase 1: `aot_compile.py`

```python
# BEFORE:
process_result = await Get(FallibleProcessResult, Process, await Get(Process, JvmProcess, process))

# AFTER:
process_obj = await Get(Process, JvmProcess, process)
process_result = await Get(FallibleProcessResult, Process, process_obj)
```

### Phase 2: `check.py`

```python
# BEFORE:
result = await Get(FallibleProcessResult, Process, await Get(Process, JvmProcess, jvm_process))

# AFTER:
process = await Get(Process, JvmProcess, jvm_process)
result = await Get(FallibleProcessResult, Process, process)
```

## Why Sequential Gets Over Intrinsics

The plan reviewer noted that while Java/Scala backends use `execute_process(**implicitly(JvmProcess(...)))`, the sequential Get pattern is:
- Simpler (no new imports needed)
- Already proven in our test.py
- More readable and maintainable
- Consistent with how test.py handles process execution

## Notes

- This change aligns with Pants best practices (no nested await Get patterns exist in the Pants codebase)
- The fix is minimal and low-risk
- The hang issue in `test_hang_repro.py` has a different root cause that needs further investigation
