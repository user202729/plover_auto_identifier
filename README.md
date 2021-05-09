# plover-auto-identifier
You don't have to keep typing `KPA*` or `RUPBD` while programming.

### Example usage

* Type `THRAEUT R*UPBD STROEBG`, `translate_stroke` is printed.
* From the next time, typing `THRAEUT STROEBG` will automatically create `translate_stroke`.
* The first step is not necessary with editor integration.

This program is available on [GitHub](https://github.com/user202729/plover_auto_identifier) 
and [PyPI](https://pypi.org/project/plover-auto-identifier/). Report bugs on GitHub.

Note that this program uses some internal API of Plover, and thus is not guaranteed to work
on any given Plover versions.

### Configuration

Create a file named `plover_auto_identifier_config.json` in Plover's configuration folder
with the content:

```json
{
"max_size": 100000000
}
```

Fields:

* `max_size`: the maximum size of a file in bytes that a file can be loaded automatically
(to read the words from). `-1` means unlimited.

### Features

* Define a dictionary entry mapped to `=auto_identifier_mark` to mark the most recently typed
word as an identifier
(only works if the identifier is stroked with no more than 10 translations)  
Note that identifiers are also mapped automatically.
* Define a dictionary entry mapped to `=auto_identifier_remove` to unmap and revert the most recently
automatically converted identifier (only works if the translation is recent)
* Send to the named pipe `\\.\pipe\plover_auto_identifier` (Windows) or
socket `/<tempdir>/plover_auto_identifier_socket` (UNIX) a Python string
being the file name to read the list of identifiers from that file (with `authkey=None`).

	The file must be encoded in UTF-8 encoding.

	The existing identifiers will be removed.

	Note that it's necessary to send only **one** message for each connection.

For example, you can place this code into `.vimrc` (UNIX system):

```vim
function s:NotifyPloverAutoIdentifier()
pythonx << EOF
import vim
from multiprocessing import connection
filepath=vim.eval('expand("%:p")')
if filepath: #if the user edit a new buffer, filepath might be empty
	try:
		c=connection.Client("/tmp/plover_auto_identifier_socket")
		try:
			c.send(("file", filepath))
		finally:
			c.close()
	except FileNotFoundError:
		pass # the plugin is not listening
	except OSError as e: # weird behavior
		print(e)
		
EOF
endfunction

augroup vimrc_notify_plover_auto_identifier
	auto!
	auto BufEnter,BufWritePost * call s:NotifyPloverAutoIdentifier()
augroup END
```

------

Idea:

* Integrate with some editor to load the word list (done)
* Also track the output for the word list. (done)
   * But if the word typed is deleted, also remove that word from the word list. (TODO)
   * That is, if there's no editor integration (TODO)
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
