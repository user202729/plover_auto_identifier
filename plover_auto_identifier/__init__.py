from typing import Dict, Tuple, TYPE_CHECKING, NamedTuple, List, MutableMapping, Sequence, Optional, Any, cast
from collections import defaultdict
import sys
import re
import json
from pathlib import Path
import argparse
import functools
import tempfile
import threading
import os

from plover.oslayer.config import CONFIG_DIR
from plover import log
from plover_build_utils.testing import CaptureOutput
from plover.translation import Translation, Translator
from plover.formatting import Formatter
from plover.steno import Stroke

from .controller import Controller

if TYPE_CHECKING:
	import plover.engine

#stored_wordlist=Path(CONFIG_DIR)/"wordlist.json"
stored_wordlist=Path(tempfile.gettempdir())/"wordlist.v3.json"

@functools.lru_cache()
def to_simple(word: str)->str:
	return re.sub(r"\W|_", "", word).lower()

def effective_no_op(word: str)->bool:
	return to_simple(word)==""

def lower_first_character(string: str)->str:
	if not string: return string
	return string[0].lower()+string[1:]

@functools.lru_cache()
def translations_to_output_1(translations: Tuple[str], initial_text: str="")->CaptureOutput:
	# Translation object can never contain macro info

	# The list should not contain replaced entries
	# so for example, [Translation(A, replaced=[Translation(B)])] is okay
	# while [Translation(B), Translation(A, replaced=[Translation(B)])] is not

	# at the moment, each element of `translations` is a `.english` translation result

	# the result (text, instructions) should not be modified,
	# otherwise the caching will return wrong result.

	# the formatter might raise some error

	output = CaptureOutput()
	formatter = Formatter(); formatter.set_output(output)
	formatter.start_attached = True
	formatter.spaces_after = False
	formatter.start_capitalized = False

	output.text = initial_text

	fake_strokes=[Stroke([])]

	if translations:
		formatter.format(undo=[], do=[
			Translation(fake_strokes, english) for english in translations
			], prev=None)

	return output

def translations_to_output(translations: List[Translation], initial_text: str="")->CaptureOutput:
	# Same restrictions as above.
	return translations_to_output_1(tuple(t.english for t in translations))

def translations_to_text_or_empty(translations: List[Translation], initial_text: str="")->str:
	translations=[*translations]
	for t in translations: assert isinstance(t, Translation)
	try:
		return translations_to_output(translations).text
	except:
		import traceback
		log.info("".join(traceback.format_exc()))
		return ""

main_instance=cast("Main", None)

def parse_identifier_mark(argument: str)->Optional[Tuple[str, int, str]]:
	# argument should be the value parsed by Plover (without the escape of "\\{}")
	match=re.fullmatch("(.*){PLOVER:AUTO_IDENTIFIER_IS_IDENTIFIER_MARK:(\d*?) (.*)}", argument)
	if not match: return None
	return match[1], int(match[2]), re.sub(r"\\([{}\\])", r"\1", match[3])

def create_identifier_mark(word: str, number_last_replace: int, last_content: str)->str:
	last_content_escaped=last_content.translate({ord(ch): "\\"+ch for ch in "\\{}"})
	result=word+"{PLOVER:AUTO_IDENTIFIER_IS_IDENTIFIER_MARK:"+str(number_last_replace)+' '+last_content_escaped+"}"
	assert parse_identifier_mark(result)==(word, number_last_replace, last_content)
	return result

configuration_file_path: Path=Path(CONFIG_DIR)/"plover_auto_identifier_config.json"

class Main:
	def __init__(self, engine: "plover.engine.StenoEngine")->None:
		self._engine=engine
		#for simplicity, it will always run now

		self._simple_to_word_modification_lock=threading.Lock()
		self._simple_to_word: MutableMapping[str, str]

		try:
			self._load_wordlist()
		except:
			self._clear_simple_to_word()

		global main_instance
		assert main_instance is None
		main_instance=self

		# TODO if there are multiple words with the same simple form, only the most recently typed can be entered
		#engine.hook_connect("send_string", self.on_send_string)
		#engine.hook_connect("send_backspaces", self.on_send_backspaces)
		#engine.hook_connect("send_key_combination", self.on_send_key_combination)

		self._temporarily_disabled: bool=False

		self._running: bool=False
		self._controller: Optional[Controller]=None

		self._config: dict={}

	def _clear_simple_to_word(self)->None:
		with self._simple_to_word_modification_lock:
			self._simple_to_word={}
			self._save_wordlist()

	def _message_cb(self, message: Any)->None:
		log.info(f"Received message: {message!r}")
		message_type, message_content=message
		assert message_type=="file"
		try:
			filename=Path(message_content)
			max_size=self._config.get("max_size", -1)
			if max_size>=0 and os.stat(filename).st_size>=max_size:
				return
			with filename.open("r", encoding='u8') as f:
				content=f.read()
			with self._simple_to_word_modification_lock:
				self._simple_to_word={
						to_simple(word): word
						for word in re.findall(r"\w+", content)
						}
				self._save_wordlist()
		#except FileNotFoundError, PermissionError, IsADirectoryError:
		except OSError:
			pass

	def _load_wordlist(self)->None:
		data=json.load(stored_wordlist.open("r"))
		with self._simple_to_word_modification_lock:
			self._simple_to_word=data["simple_to_word"]

	def _save_wordlist(self)->None:
		assert self._simple_to_word_modification_lock.locked()
		json.dump({
			"simple_to_word": self._simple_to_word,
			},
			stored_wordlist.open("w"), indent="\t")

	def on_translated(self, old, new)->None:
		self._engine._queue.put((self.after_translated, [], {}))
		# on_translated hook is called inside formatter.format(), before output is printed
		# recursive call causes weird errors
		# generally, private method access causes weird errors
		# but there's not really any way...

	def after_translated(self)->None:
		translator=self._engine._translator
		#translator.translate_translation(...)
		#translator.translate_stroke(...)

		translations: List[Translation]=translator.get_state().translations

		# only consider 10 last translations
		part=translations[max(len(translations)-10, 0):]

		# ignore any translation that produces a combo or a command, or a raw stroke (likely a misstroke)
		# NOTE therefore, currently it's not possible to nest multiple identifiers
		for i in range(len(part)-1, -1, -1):
			t=part[i]
			if (
					any(f.combo or f.command for f in t.formatting)
					or not t.english
					):
				part=part[i+1:]
				break
			else:
				assert t.english
				assert parse_identifier_mark(t.english) is None
				# (because currently the identifier mark is implemented as a command)

		if not part: return


		# add new words...?
		full_output=translations_to_output(part)
		# TODO remove words in output on undoing


		splitter_matches=[*re.finditer(r"(\W+)", full_output.text)]
		if len(splitter_matches)>=2: # there's a word before the candidate
			#=> because moving left and right (right before the first operation) might corrupt the actual typed word

			#  abc) -- -- -- (def) ++ -- ghi
			#	  ~~~~~~~~~~~~	 ~~~~~~~~
			#	  ^ splitter_matches[-2]
			#					 ^ splitter_matches[-1]

			# select the penultimate => do not add partial word
			# condition: it must form a complete word (so no "a_[b_c]")

			candidate: str=full_output.text[splitter_matches[-2].end():splitter_matches[-1].start()]
			target1: str=full_output.text[:splitter_matches[-2].end()].rstrip()
			target2: str=full_output.text[:splitter_matches[-1].start()].rstrip()

			candidate_simple=to_simple(candidate)
			if self._simple_to_word.get(candidate_simple, None)!=candidate:
				# condition: There must be a subset of parts that form the candidate

				a: Optional[int]=None
				b: Optional[int]=None
				for i in range(1, len(part)): #not empty, not full
					cur=translations_to_text_or_empty(part[:i]).rstrip()
					if cur==target1 and a is None:
						a=i #choose the first one (handle the case where there's a upper-next at the beginning)
					elif cur==target2:
						b=i #choose the first one (why?)
						break

					# ======some test cases======

					# a b ^ c d
					# =>
					# a     (**=a)
					# a b
					# a b
					# a bC  (**=b)
					# a bC d

					# okay, uniquely determined

					# a <space> ^ b ^ c d
					# a
					# a<space>   (** should be =a)
					# a<space>
					# a B
					# a B
					# a BC       (=b, okay, uniquely determined)
					# a BC d


					# Iterate over all possible combinations? Weird.
					# * Do not add single-word (in addition to single-translation) entries into
					# the wordlist? (what's the point?)
					

				if a is not None and b is not None:
					part1=part[a:b]
					if (translations_to_text_or_empty(part1).strip()==candidate.strip() and
							# condition: the user has spent additional effort to form the word
							# note: this turns out to be a hard condition to check
							# and currently might not be very correct
							any(effective_no_op(translations_to_text_or_empty([t])) for t in part1)
							):
						log.info(f"Add {candidate!r}")
						with self._simple_to_word_modification_lock:
							self._simple_to_word[candidate_simple]=candidate
							self._save_wordlist()


		# find longest chunk that can be merged (not at all efficient...)

		for i in range(len(part)-1, -1, -1):
			t=part[i]
			if effective_no_op(translations_to_text_or_empty([t])):
				part=part[i+1:]
				break
		if not part: return

		for i in range(0, len(part)-1):
			part1=part[i:] # find longest part1 possible
			assert len(part1)>=2
			output=translations_to_output(part1)
			assert all(
					instruction_type not in ("c", "e")
					for instruction_type, instruction_data in output.instructions)

			simple_form=to_simple(output.text)

			if (
					full_output.text.endswith(output.text) and
					# not partial-translation or modifying prefixes(?)/suffixes

					re.fullmatch(r"(\w| )+", output.text) and
					# the user might have a stroke that outputs "+1" which is not no-op,
					# but "a+1" for example should not be transformed to "a1"

					not re.fullmatch(r"\w+", output.text) and
					# only translate if (old) output consists of multiple words
					# the most common type of false positive is fingerspelled entries 

					(
						full_output.text==output.text or
						re.match(r"\W", full_output.text[-len(output.text)-1])
						) # that component is not partial (such as _[connection])

					and
					simple_form in self._simple_to_word
					and 

					# there's no "no-op" translations

					#len(set(
					#	 to_simple(translations_to_output(part1[:i]).text)
					#	 for i in range(len(part1)+1)
					#	 ))==len(part1)+1

					all(not effective_no_op(translations_to_text_or_empty([t])) for t in part1)

					):
				replaced_word=self._simple_to_word[simple_form]
				assert "{" not in replaced_word
				assert "}" not in replaced_word

				log.info(f"Replace {output.text!r} -> {replaced_word!r}")

				new_translation=Translation(
						outline=[Stroke([])],
						translation=create_identifier_mark(
							replaced_word,
							len(part1[-1].replaced),
							part1[-1].english
						)
						)
				new_translation.replaced=part1[:-1]+part1[-1].replaced

				translator.untranslate_translation(part1[-1])
				translator.translate_translation(new_translation)
				translator.flush()

				break


	def start(self)->None:
		self._running=True
		self._engine.hook_connect("translated", self.on_translated)
		instance=".plover_auto_identifier"
		self._controller=Controller(instance=instance, authkey=None)
		self._controller.__enter__()

		if not self._controller.is_owner:
			log.debug("Force cleanup plover auto identifier socket")
			if not self._controller.force_cleanup():
				raise RuntimeError("Another instance?")

			self._controller=Controller(instance=instance, authkey=None)
			self._controller.__enter__()

		self._controller.start(self._message_cb)
		try:
			self._config=json.loads(configuration_file_path.read_text(encoding='u8'))
		except FileNotFoundError:
			pass

	def stop(self)->None:
		self._running=False
		self._engine.hook_disconnect("translated", self.on_translated)
		assert self._controller
		self._controller.stop()
		self._controller.__exit__(None, None, None)
		self._controller=None

	def is_identifier_mark(self, engine: "plover.engine.StenoEngine", argument: str)->None:
		# this isn't actually processed, just so that latter functions can recognize
		# the identifiers/combined words converted by this plugin.
		pass

	def remove_identifier(self, translator: Translator, stroke: Stroke, argument: str)->None:
		"""Remove the most recently converted identifier."""
		assert not argument
		self._engine._queue.put((self.after_remove_identifier, [translator], {}))

	def after_remove_identifier(self, translator: Translator)->None:
		translations: List[Translation]=translator.get_state().translations
		#for t in reversed(translations[-10:]):
		for i in range(len(translations)-1, max(-1, len(translations)-11), -1):
			t=translations[i]
			if t.english:
				match=parse_identifier_mark(t.english)
				if match is not None:
					word, number_last_replace, last_content=match

					word_simple=to_simple(word)
					if self._simple_to_word.get(word_simple, None)==word:
						del self._simple_to_word[word_simple]

					pending=[]
					while t is not translations[-1]:
						pending.append(translations[-1])
						translator.untranslate_translation(translations[-1])
						translations=translator.get_state().translations
					assert number_last_replace<=len(t.replaced)
					translator.untranslate_translation(t)
					translations=translator.get_state().translations

					new_translation=Translation(
							[Stroke([])], #TODO this is incorrect
							last_content)
					new_translation.replaced=translations[len(translations)-number_last_replace:]
					translator.translate_translation(new_translation)

					for t in reversed(pending):
						translator.translate_translation(t)

					translator.flush()
					return
		raise Exception("No recently-translated identifier found!")

	def mark_as_identifier(self, translator: Translator, stroke: Stroke, argument: str)->None:
		translations: List[Translation]=translator.get_state().translations
		match=re.search(r"(\w+)$", translations_to_text_or_empty(translations))
		if not match:
			raise Exception("Most recent word is not a word, or is too long")

		word=match[0]
		log.info(f"Add {word!r} to wordlist")
		with self._simple_to_word_modification_lock:
			self._simple_to_word[to_simple(word)]=word
			self._save_wordlist()
		

def is_identifier_mark(engine: "plover.engine.StenoEngine", argument: str)->None:
	main_instance.is_identifier_mark(engine, argument)

def remove_identifier(translator: Translator, stroke: Stroke, argument: str)->None:
	main_instance.remove_identifier(translator, stroke, argument)

def mark_as_identifier(translator: Translator, stroke: Stroke, argument: str)->None:
	main_instance.mark_as_identifier(translator, stroke, argument)
