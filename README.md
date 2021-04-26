# plover-auto-identifier
You don't have to keep typing `KPA*` or `RUPBD` while programming.

Example usage:

* Type `THRAEUT R*UPBD STROEBG`, `translate_stroke` is printed.
* From the next time, typing `THRAEUT STROEBG` will automatically create `translate_stroke`.

This program is available on [GitHub](https://github.com/user202729/plover_auto_identifier) 
and PyPI. Report bugs on GitHub.

Note that this program uses some internal API of Plover, and thus is not guaranteed to work
on any given Plover versions.

------

Idea:

* Integrate with some editor to load the word list
* Also track the output for the word list.
   * But if the word typed is deleted, also remove that word from the word list.
   * That is, if there's no editor integration
* Given a word list...
   * If user stroke A/B/C -> "a b c", automatically convert it to "aBC" (assuming camel case)
   * Then if user press `*` there are two options
      * Revert to "a b c" (note: do not reform the word immediately!)
      * Revert to "a b" (which is the one that makes the most sense with Plover's default meaning of undo)
         * Note: handle the case that the last "formed" word/translation has replaced entries!
      * Delete the whole word
   * In the latter 2 cases there should be a stroke that undo the conversion
   * Do not convert anything if there's any no-op stroke!
      * No-op: for example delete-space `TK-LS`, camel-case, or underscore.
   * Only change new content...?
      * For example, if the user stroke A/B, then add aB to the word list,
        then undo to the point with that content "a b" it should not be spontaneously changed to "aB".
      * But what if the user delete the "b" then stroke it back?
