# Natural Language Toolkit: Language Models
#
# Copyright (C) 2001-2023 NLTK Project
# Authors: Ilia Kurenkov <ilia.kurenkov@gmail.com>
# URL: <https://www.nltk.org/>
# For license information, see LICENSE.TXT
"""Language Model Interface."""

import random
import warnings
from abc import ABCMeta, abstractmethod
from bisect import bisect
from itertools import accumulate

from nltk import FreqDist
from nltk.lm.counter import NgramCounter
from nltk.lm.util import log_base2
from nltk.lm.vocabulary import Vocabulary


class Smoothing(metaclass=ABCMeta):
    """Ngram Smoothing Interface

    Implements Chen & Goodman 1995's idea that all smoothing algorithms have
    certain features in common. This should ideally allow smoothing algorithms to
    work both with Backoff and Interpolation.
    """

    def __init__(self, vocabulary, counter):
        """
        :param vocabulary: The Ngram vocabulary object.
        :type vocabulary: nltk.lm.vocab.Vocabulary
        :param counter: The counts of the vocabulary items.
        :type counter: nltk.lm.counter.NgramCounter
        """
        self.vocab = vocabulary
        self.counts = counter

    @abstractmethod
    def unigram_score(self, word):
        raise NotImplementedError()

    @abstractmethod
    def alpha_gamma(self, word, context):
        raise NotImplementedError()


def _mean(items):
    """Return average (aka mean) for sequence of items."""
    return sum(items) / len(items)


def _random_generator(seed_or_generator):
    if isinstance(seed_or_generator, random.Random):
        return seed_or_generator
    return random.Random(seed_or_generator)


def _weighted_choice(population, weights, random_generator=None):
    """Like random.choice, but with weights.

    Heavily inspired by python 3.6 `random.choices`.
    """
    if not population:
        raise ValueError("Can't choose from empty population")
    if len(population) != len(weights):
        raise ValueError("The number of weights does not match the population")
    cum_weights = list(accumulate(weights))
    total = cum_weights[-1]
    threshold = random_generator.random()
    return population[bisect(cum_weights, total * threshold)]


class LanguageModel(metaclass=ABCMeta):
    """ABC for Language Models.

    Cannot be directly instantiated itself.

    """

    def __init__(self, order, vocabulary=None, counter=None):
        """Creates new LanguageModel.

        :param vocabulary: If provided, this vocabulary will be used instead
            of creating a new one when training.
        :type vocabulary: `nltk.lm.Vocabulary` or None
        :param counter: If provided, use this object to count ngrams.
        :type counter: `nltk.lm.NgramCounter` or None
        :param ngrams_fn: If given, defines how sentences in training text are turned to ngram
            sequences.
        :type ngrams_fn: function or None
        :param pad_fn: If given, defines how sentences in training text are padded.
        :type pad_fn: function or None
        """
        self.order = order
        if vocabulary and not isinstance(vocabulary, Vocabulary):
            warnings.warn(
                f"The `vocabulary` argument passed to {self.__class__.__name__!r} "
                "must be an instance of `nltk.lm.Vocabulary`.",
                stacklevel=3,
            )
        self.vocab = Vocabulary() if vocabulary is None else vocabulary
        self.counts = NgramCounter() if counter is None else counter

    def fit(self, text, vocabulary_text=None):
        """Trains the model on a text.

        :param text: Training text as a sequence of sentences.

        """
        if not self.vocab:
            if vocabulary_text is None:
                raise ValueError(
                    "Cannot fit without a vocabulary or text to create it from."
                )
            self.vocab.update(vocabulary_text)
        self.counts.update(self.vocab.lookup(sent) for sent in text)

    def score(self, word, context=None):
        """Masks out of vocab (OOV) words and computes their model score.

        For model-specific logic of calculating scores, see the `unmasked_score`
        method.
        """
        return self.unmasked_score(
            self.vocab.lookup(word), self.vocab.lookup(context) if context else None
        )

    @abstractmethod
    def unmasked_score(self, word, context=None):
        """Score a word given some optional context.

        Concrete models are expected to provide an implementation.
        Note that this method does not mask its arguments with the OOV label.
        Use the `score` method for that.

        :param str word: Word for which we want the score
        :param tuple(str) context: Context the word is in.
            If `None`, compute unigram score.
        :param context: tuple(str) or None
        :rtype: float
        """
        raise NotImplementedError()

    def logscore(self, word, context=None):
        """Evaluate the log score of this word in this context.

        The arguments are the same as for `score` and `unmasked_score`.

        """
        return log_base2(self.score(word, context))

    def context_counts(self, context):
        """Helper method for retrieving counts for a given context.

        Assumes context has been checked and oov words in it masked.
        :type context: tuple(str) or None

        """
        return (
            self.counts[len(context) + 1][context] if context else self.counts.unigrams
        )

    def contains_UNKs(self, ngram):
        """Helper method to indicate whether an ngram contains an UNK token or not.

        :param tuple(str) ngram: ngram tuples
        :rtype: bool
        
        """
        return any([self.counts.unigrams[ng] for ng in ngram])

    
    def entropy(self, text_ngrams):
        """Calculate cross-entropy of model for given evaluation text.

        This implementation is based on the Shannon-McMillan-Breiman theorem,
        as used and referenced by Dan Jurafsky and Jordan Boyd-Graber.

        :param Iterable(tuple(str)) text_ngrams: A sequence of ngram tuples.
        :rtype: float

        """
        return -1 * _mean(
            [self.logscore(ngram[-1], ngram[:-1]) for ngram in text_ngrams]
        )

    def perplexity(self, text_ngrams):
        """Calculates the perplexity of the given text.

        This is simply 2 ** cross-entropy for the text, so the arguments are the same.

        """
        return pow(2.0, self.entropy(text_ngrams))

    def entropy_extended(self, text_ngrams, length_normalisation=True, rel_freq_weighting=False):
        """Calculate cross-entropy of model for given evaluation text.

        This implementation is based on the standard Shannon entropy,
        extended with the possibility to normalise the entropy by sentence length,
        and/or weight the output by the relative frequency of the ngram.
        In case of <UNK> tokens, weight with the minimum relative frequency in the dataset.

        :param Iterable(tuple(str)) text_ngrams: A sequence of ngram tuples.
        :param bool length_normalisation: A boolean to indicate whether you want to
            normalise by sequence length.
        :param bool rel_freq_weighting: A boolean to indicate whether you want to
            weight probabilities by ngram relative frequency.
        :rtype: float

        """
        
        if rel_freq_weighting:
            fdist = FreqDist()
        
        probabilities = []    
        for ngram in text_ngrams:
            probabilities.append(self.score(ngram[-1], ngram[:-1]))
            if rel_freq_weighting:
                fdist[' '.join(ngram)] += 1
        
        if rel_freq_weighting:
            total_freq_fdist = sum(fdist.values())
            rel_fdist = {key: fdist[key]/total_freq_fdist for key in fdist.keys()}
            min_freq_rel_fdist = min(rel_fdist.values())
            
            weighted_probabilities = []
            for prob, ngram in zip(probabilities, text_ngrams):
                if self.contains_UNKs(ngram):
                    prob *= min_freq_rel_fdist
                else:
                    prob *= rel_fdist[ngram]
                weighted_probabilities.append(prob)
            probabilities = weighted_probabilities
        
        entropy_extended = -1 * sum([prob * log_base2(prob) for prob in probabilities])
        
        if length_normalisation:
            entropy_extended /= len(probabilities)
            
        return entropy_extended

    def perplexity_extended(self, text_ngrams, length_normalisation=True, rel_freq_weighting=False):
        """Calculates the perplexity of the given text based on the extended version of the entropy method.

        This is simply 2 ** cross-entropy for the text, so the arguments are the same.

        """
        return pow(2.0, self.entropy_extended(text_ngrams, length_normalisation, rel_freq_weighting))
        
    def generate(self, num_words=1, text_seed=None, random_seed=None):
        """Generate words from the model.

        :param int num_words: How many words to generate. By default 1.
        :param text_seed: Generation can be conditioned on preceding context.
        :param random_seed: A random seed or an instance of `random.Random`. If provided,
            makes the random sampling part of generation reproducible.
        :return: One (str) word or a list of words generated from model.

        Examples:

        >>> from nltk.lm import MLE
        >>> lm = MLE(2)
        >>> lm.fit([[("a", "b"), ("b", "c")]], vocabulary_text=['a', 'b', 'c'])
        >>> lm.fit([[("a",), ("b",), ("c",)]])
        >>> lm.generate(random_seed=3)
        'a'
        >>> lm.generate(text_seed=['a'])
        'b'

        """
        text_seed = [] if text_seed is None else list(text_seed)
        random_generator = _random_generator(random_seed)
        # This is the base recursion case.
        if num_words == 1:
            context = (
                text_seed[-self.order + 1 :]
                if len(text_seed) >= self.order
                else text_seed
            )
            samples = self.context_counts(self.vocab.lookup(context))
            while context and not samples:
                context = context[1:] if len(context) > 1 else []
                samples = self.context_counts(self.vocab.lookup(context))
            # Sorting samples achieves two things:
            # - reproducible randomness when sampling
            # - turns Mapping into Sequence which `_weighted_choice` expects
            samples = sorted(samples)
            return _weighted_choice(
                samples,
                tuple(self.score(w, context) for w in samples),
                random_generator,
            )
        # We build up text one word at a time using the preceding context.
        generated = []
        for _ in range(num_words):
            generated.append(
                self.generate(
                    num_words=1,
                    text_seed=text_seed + generated,
                    random_seed=random_generator,
                )
            )
        return generated
