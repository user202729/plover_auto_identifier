# TODO there's no way to break words (S-P or TK-LS or KPA doesn't have any effect)
# TODO I typed a raw stroke that contain -S, and now all "s" are capitalized to "S"?...
# TODO better way to keep track of issues
# TODO handle prefix/suffix strokes better?...
from typing import Dict, Tuple, TYPE_CHECKING, NamedTuple, List, MutableMapping
from collections import defaultdict
import subprocess
import sys
import re
import json
from pathlib import Path
from plover import log
from plover.macro.undo import undo
import argparse
import shlex
from plover.oslayer.config import CONFIG_DIR
from .delete_char import delete_char
import functools

if TYPE_CHECKING:
	import plover.engine

#stored_wordlist=Path(CONFIG_DIR)/"wordlist.json"
stored_wordlist=Path("/tmp/wordlist.v2.json")
try:
	logfile=Path("/tmp/L").open("w", buffering=1)
	L=functools.partial(print, file=logfile)
	# could use log.debug too but there's no way to turn off Plover's debug
except:
	pass

class Token:
	def __init__(self, is_word: bool, content: str="")->None:
		self.content: str=content # might be a word or non-word characters
		self.is_word: bool=is_word
		self.defining: bool=False
		# defining: first occurrence of that word?
		# delete the word from wordlist if this word is deleted

def to_simple(word: str)->str:
	return re.sub(r"\W|_", "", word).lower()

def lower_first_character(string: str)->str:
	if not string: return string
	return string[0].lower()+string[1:]

class Main:
	def __init__(self, engine: "plover.engine.StenoEngine")->None:
		self._engine=engine
		#for simplicity, it will always run now

		self._buffer: List[Token]=[]

		try:
			self._load_wordlist()
		except:
			self._simple_to_word, self._simple_length_bound=defaultdict(list), 0
			self._save_wordlist()

		self._simple_to_word: MutableMapping[str, List[str]]
		# TODO if there are multiple words with the same simple form, only the most recently typed can be entered
		engine.hook_connect("send_string", self.on_send_string)
		engine.hook_connect("send_backspaces", self.on_send_backspaces)
		engine.hook_connect("send_key_combination", self.on_send_key_combination)

		self._temporarily_disabled: bool=False

	def _load_wordlist(self)->None:
		data=json.load(stored_wordlist.open("r"))
		self._simple_to_word=defaultdict(list, data["simple_to_word"])
		self._simple_length_bound=data["simple_length_bound"]

	def _save_wordlist(self)->None:
		json.dump({
			"simple_to_word": self._simple_to_word,
			"simple_length_bound": self._simple_length_bound
			},
			stored_wordlist.open("w"), indent="\t")


	def on_send_string(self, s: str)->None:
		L("Get: send string", s)
		try:
			parts=re.split(r"(\W+)", s)
			assert len(parts)%2==1
			buf=self._buffer
			for index, part in enumerate(parts):
				if not part: continue
				is_word=index%2==0

				if buf and buf[-1].is_word==is_word:
					buf[-1].content+=part
				else:
					if buf and buf[-1].is_word:
						new_word=buf[-1].content
						new_word_simple=to_simple(new_word)
						if (
								lower_first_character(new_word)!=new_word_simple # avoid lowercase being automatically converted to uppercase
								# there are cases of normal and uppercased abbreviation TODO
								and
								new_word not in self._simple_to_word.get(new_word_simple, ())
								):
							# (in) takes O(n), but there should not be a lot of conflicts
							# see also todo above
							buf[-1].defining=True
							self._simple_to_word[new_word_simple].append(new_word)
							self._simple_length_bound=max(self._simple_length_bound, len(new_word_simple))
							L(f"Add {new_word_simple} -> {new_word}")
							self._save_wordlist()

							#L(self._simple_to_word, self._simple_length_bound)

					buf.append(Token(is_word, part))

			#try to merge just-typed words
			component=""
			delete_content=""
			if buf and buf[-1].is_word:
				for i in range(len(buf)-1, -1, -1):
					part=buf[i].content
					assert part
					delete_content=part+delete_content
					if not buf[i].is_word and not part.isspace(): break
					if buf[i].defining: break
					if not buf[i].is_word: continue
					component=to_simple(part)+component
					if len(component)>self._simple_length_bound: break

					L(f"* {component=} {delete_content=}")

					if component in self._simple_to_word:
						assert self._simple_to_word[component]
						replace=self._simple_to_word[component][-1]
						if delete_content==replace:
							break

						# TODO current bug: if user types ". And", all subsequent "and" will be changed to "And"
						# Idea: detect zero-effect strokes/translations/actions (but not sent-string)

						# TODO for after-output space placement users perhaps it should not be defined that quickly?

						# TODO? there's no easy way to determine which stroke/translation/action
						# corresponds to which output string...

						#engine._translator
						assert all(x not in replace for x in "{}\\")

						from plover.translation import Translation
						L(f"Do replace {delete_content} -> {replace}")
						if self._temporarily_disabled:
							L("Huh?")
						else:
							from plover.steno import Stroke
							self._temporarily_disabled=True
							#assert self._engine.translator_state.translations
							#self._engine.translator_state.translations.pop()

							translator=self._engine._translator

							assert translator.get_state().translations
							last_translation=translator.get_state().translations[-1]
							L(last_translation, last_translation.__dict__)

							translator._undo(last_translation)

							new_translation=Translation(
									outline=[Stroke([])],
									translation=
									last_translation.english+
									"{:plover_auto_identifier_delete_char:" + delete_content + "}"+
									replace
									)
							new_translation.replaced=last_translation.replaced

							#undo(translator, Stroke([]), '')
							translator._do(new_translation)
							# !! do not use untranslate_translation and translate_translation
							# (they handle replaced, which is undesired)
							# although perhaps that's fine too?
							
							translator.flush()

							self._temporarily_disabled=False

						break
		except:
			# TODO why Plover can't print traceback already?
			import traceback
			L(traceback.format_exc())

	def on_send_backspaces(self, b: int)->None:
		L("Get bksp = ",b)
		assert b>0
		buf=self._buffer
		while buf and b:
			assert not buf[-1].defining
			if b>=len(buf[-1].content):
				#delete item
				b-=len(buf[-1].content)
				buf.pop()

				#remove defining status of buf[-1], if it was
				if buf and buf[-1].defining:
					assert buf[-1].is_word
					word=buf[-1].content
					word_simple=to_simple(word)
					self._simple_to_word[word_simple].remove(word)
					if not self._simple_to_word[word_simple]:
						del self._simple_to_word[word_simple]
					self._save_wordlist()
					buf[-1].defining=False
			else:
				assert b>0
				buf[-1].content=buf[-1].content[:-b]
				break

	def on_send_key_combination(self, c: str)->None:
		self._buffer=[]
		L("Clear state")
		# make keys permanent, possibly except last one typed (TODO?)

	def start(self)->None:
		pass

	def stop(self)->None:
		pass
