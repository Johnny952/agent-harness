# `forward.ts` can count the api's reads and the board-service's writes with the same word

**When it applies:** you add or remove a board-service write in
`front/src/lib/api/forward.ts` and sweep its prose counts.

**Status:** unconfirmed. Reported by T-125-S3's arquitecto; checked against the
tree by its auditor.

## Symptom

At T-125-S3's base both counts read "twenty-one", in the same file:

```
"Twenty-one console paths map one-to-one onto twenty-one api routes"   (the api reads)
"with the eighteen above they make twenty-one writes"                   (the board writes)
```

## Why

`FORWARDED`'s api routes and the board-service writes are two lists counted in
prose, and nothing ties either word to a length. A sweep for the word after
adding a write also bumps the read count, and `resolveTarget`'s 404 message
(pinned by `forward.test.ts`) carries the read one, so the suite then fails on
a count that should not have moved.

## Rule

Bump only the board-write sentences (the module doc's board paragraphs,
`boardTarget`, `forwardBoard`, the "exact pairs" comment in
`forwardApiRequest`); leave every "routes the console forwards" count alone.
The read-count sweep itself is
[the-forwarded-route-count-is-prose-and-derived-from-nothing](the-forwarded-route-count-is-prose-and-derived-from-nothing.md).

## Evidence

`grep -n -i "twenty-one\|twenty-two" front/src/lib/api/forward.ts` on T-125-S3's
branch: the writes read twenty-two after the approve, the reads still
twenty-one. Inbox entry
`/data/.hive/learnings/inbox/T-125-S3-forward-ts-has-two-twenty-ones.md`.
