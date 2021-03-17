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

if TYPE_CHECKING:
	import plover.engine

stored_wordlist=Path(CONFIG_DIR)/"wordlist.json"

class Token:
	def __init__(self, is_word: bool, content: str="")->None:
		self.content: str=content # might be a word or non-word characters
		self.is_word: bool=is_word
		self.defining: bool=False
		# defining: first occurrence of that word?
		# delete the word from wordlist if this word is deleted

def to_simple(word: str)->str:
	return re.sub(r"\W|_", "", word).lower()

class Main:
	def __init__(self, engine: "plover.engine.StenoEngine")->None:
		self._engine=engine
		#for simplicity, it will always run now

		self._buffer: List[Token]=[]
		self._simple_length_bound=0

		self._simple_to_word: MutableMapping[str, List[str]]=defaultdict(list)
		# TODO if there are multiple words with the same simple form, they will be in an arbitrary order
		engine.hook_connect("send_string", self.on_send_string)
		engine.hook_connect("send_backspaces", self.on_send_backspaces)
		engine.hook_connect("send_key_combination", self.on_send_key_combination)

		self._temporarily_disabled: bool=False


	def on_send_string(self, s: str)->None:
		print("Get: send string", s)
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
						if new_word!=new_word_simple and new_word not in self._simple_to_word.get(new_word_simple, ()):
							# (in) takes O(n), but there should not be a lot of conflicts
							# see also todo above
							buf[-1].defining=True
							self._simple_to_word[new_word_simple].append(new_word)
							self._simple_length_bound=max(self._simple_length_bound, len(new_word_simple))
							print(f"Add {new_word_simple} -> {new_word}")

					buf.append(Token(is_word, part))

			#try to merge just-typed words
			component=""
			delete_content=""
			for i in range(len(buf)-1, -1, -1):
				part=buf[i].content
				assert part
				delete_content=part+delete_content
				if not buf[i].is_word and not part.isspace(): break
				if buf[i].defining: break
				if not buf[i].is_word: continue
				component=to_simple(part)+component
				if len(component)>self._simple_length_bound: break

				print(f"* {component=} {delete_content=}")

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
					print(f"Do replace {delete_content} -> {replace}")
					if self._temporarily_disabled:
						print("Huh?")
					else:
						from plover.steno import Stroke
						self._temporarily_disabled=True
						#assert self._engine.translator_state.translations
						#self._engine.translator_state.translations.pop()
						undo(self._engine._translator, Stroke([]), '')
						self._engine._translator.translate_translation(Translation(
							outline=[Stroke([])],
							translation="{:plover_auto_identifier_delete_char:" + delete_content + "}"
							+ replace
							))
						self._engine._translator.flush()
						self._temporarily_disabled=False

					break
		except:
			import traceback
			traceback.print_exc()

	def on_send_backspaces(self, b: int)->None:
		print("Get bksp = ",b)
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
					buf[-1].defining=False
			else:
				assert b>0
				buf[-1].content=buf[-1].content[:-b]
				break

	def on_send_key_combination(self, c: str)->None:
		self._buffer=[]
		print("Clear state")
		# make keys permanent, possibly except last one typed (TODO?)

	def start(self)->None:
		pass

	def stop(self)->None:
		pass
