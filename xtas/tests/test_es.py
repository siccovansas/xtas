from nose.tools import assert_equal, assert_in
from unittest import SkipTest
import logging
from contextlib import contextmanager

from elasticsearch import Elasticsearch, client

ES_TEST_INDEX = "xtas__unittest"
ES_TEST_TYPE = "unittest_doc"


@contextmanager
def clean_es():
    "provide a clean elasticsearch instance for unittests"
    es = Elasticsearch()
    if not ES_TEST_INDEX:
        raise SkipTest("ES_TEST_INDEX not given, skipping elastic tests")
    es = Elasticsearch()
    indexclient = client.indices.IndicesClient(es)
    if not es.ping():
        raise SkipTest("ElasticSearch host not found, skipping elastic tests")
    # check version number
    status, response = es.transport.perform_request("GET", "/")
    version_number = response['version']['number']
    if status != 200 or not (version_number.startswith("1.") and int(version_number.split(".")[1]) >= 2):
        raise SkipTest("Wrong version number or response: {status} "
                       + "/ {response}".format(**locals()))
    logging.info("deleting and recreating index {ES_TEST_INDEX}"
                 " at {ES_TEST_HOST}")
    if indexclient.exists(ES_TEST_INDEX):
        indexclient.delete(ES_TEST_INDEX)
    indexclient.create(ES_TEST_INDEX)
    try:
        yield es
    finally:
        indexclient.delete(ES_TEST_INDEX)


def test_fetch():
    "Test whether tasks.fetch works as documented"
    from xtas.tasks.es import fetch, es_document
    # if doc is a string, fetching should return the string
    assert_equal(fetch("Literal string"), "Literal string")
    # index a document and fetch it with an es_document
    with clean_es() as es:
        d = es.index(index=ES_TEST_INDEX, doc_type=ES_TEST_TYPE,
                     body={"text": "test"})
        doc = es_document(ES_TEST_INDEX, ES_TEST_TYPE, d['_id'], "text")
        assert_equal(fetch(doc), "test")


def test_query_batch():
    "Test getting multiple documents in a batch"
    from xtas.tasks.es import fetch_query_batch
    with clean_es() as es:
        es.index(index=ES_TEST_INDEX, doc_type=ES_TEST_TYPE,
                 body={"text": "test", "test": "batch"})
        es.index(index=ES_TEST_INDEX, doc_type=ES_TEST_TYPE,
                 body={"text": "test2", "test": "batch"})
        es.index(index=ES_TEST_INDEX, doc_type=ES_TEST_TYPE,
                 body={"test": "batch"})
        client.indices.IndicesClient(es).flush()
        b = fetch_query_batch(ES_TEST_INDEX, ES_TEST_TYPE,
                              query={"term": {"test": "batch"}}, field="text")
        assert_equal(set(b), {"test", "test2"})


def test_store_get_result():
    "test whether results can be stored and retrieved"
    from xtas.tasks.es import (
        store_single,
        get_single_result,
        get_tasks_per_index,
        fetch_documents_by_task,
        fetch_results_by_document,
        fetch_query_details_batch
        )
    idx, typ = ES_TEST_INDEX, ES_TEST_TYPE
    with clean_es() as es:
        id = es.index(index=idx, doc_type=typ, body={"text": "test"})['_id']
        assert_equal(get_single_result("task1", idx, typ, id), None)

        store_single("task1_result", "task1", idx, typ, id)
        client.indices.IndicesClient(es).flush()
        assert_equal(get_single_result("task1", idx, typ, id), "task1_result")
        assert_in("task1", get_tasks_per_index(idx, typ))
        # test second result and test non-scalar data
        task2_result = {"a": {"b": ["c", "d"]}}
        store_single(task2_result, "task2", idx, typ, id)
        client.indices.IndicesClient(es).flush()
        assert_equal(get_single_result("task1", idx, typ, id), "task1_result")
        assert_equal(get_single_result("task2", idx, typ, id), task2_result)
        query = {"match": {"b": {"query": "c"}}}
        assert_equal(len(fetch_documents_by_task(idx, typ, query, "task2")),
                     1)
        query = {"match": {"text": {"query": "test"}}}
        results = fetch_results_by_document(idx, typ, query, "task2")
        assert_equal(len(results), 1)
        results = fetch_query_details_batch(idx, typ, query, True)
        assert_in("task1", results[0][1])
        assert_in("task2", results[0][1])
        results = fetch_query_details_batch(idx, typ, query,
                                            tasknames=["task2"])
        assert_in("task2", results[0][1])
        # store a task result under an existing task, check that it is replaced
        store_single("task1_result2", "task1", idx, typ, id)
        client.indices.IndicesClient(es).flush()
        assert_equal(get_single_result("task1", idx, typ, id), "task1_result2")
        assert_equal(get_single_result("task2", idx, typ, id), task2_result)

        # check that the original document is intact
        src = es.get_source(index=idx, doc_type=typ, id=id)
        assert_equal(src['text'], "test")
