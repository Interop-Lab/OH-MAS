# Registry additions (frozen before any run)

Source: HapRepair public release (commit 2df699a); no OH-MAS experience/templates/reference patches/instance hints.

| rule | needsMoreContext | rationale |
|---|---|---|
| @performance/hp-arkts-no-use-any-export-other | False | fix rewrites export statements; same-file definition/usage context cannot supply the other module; HapRepair ships negative page + 3 example rows |
| @previewer/mandatory-default-value-for-local-initialization | False | single declaration-site edit |
| @hw-stylistic/quotes | False | single-line formatting |
| @hw-stylistic/operator-linebreak | False | single-line formatting |
| @hw-stylistic/no-tabs | False | single-line formatting |
| @hw-stylistic/no-multi-spaces | False | single-line formatting |
| @hw-stylistic/max-len | False | single-line formatting |
| @hw-stylistic/space-before-blocks | False | single-line formatting |
| @hw-stylistic/space-infix-ops | False | single-line formatting |

placeholder negative pages (content unread): ['mandatory-default-value-for-local-initialization.ets', 'quotes.ets', 'operator-linebreak.ets', 'no-tabs.ets', 'no-multi-spaces.ets', 'max-len.ets', 'space-before-blocks.ets', 'space-infix-ops.ets']
