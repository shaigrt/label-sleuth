import { useState, useEffect, useRef } from 'react';
import { resetSearchResults, searchKeywords } from '../../DataSlice.jsx';
import {
    setFocusedState,
    fetchCertainDocument,
    setIsDocLoaded,
    setSearchedIndex,
} from '../../DataSlice';
import { useDispatch, useSelector } from 'react-redux';

const useSearchElement = () => {

    const workspace = useSelector(state => state.workspace)
    const isDocLoaded = useSelector(state => state.workspace.isDocLoaded)
    const [isIsSearchActive, setIsSearchActive] = useState(false)
    const focusedIndex = useSelector(state => state.workspace.focusedIndex)
    const searchedIndex = useSelector(state => state.workspace.searchedIndex)
    const dispatch = useDispatch()
    const [searchInput, setSearchInput] = useState("");
    const textInput = useRef(null);

    const handleSearch = () => {
        if (searchInput) {
            dispatch(searchKeywords({ keyword: searchInput }))
        }
    }

    const clearSearchInput = () => {
        setSearchInput("")
        dispatch(resetSearchResults())
        if (textInput.current) {
            textInput.current.value = ""
        }
    }

    const handleSearchInputChange = (event) => {
        setSearchInput(event.target.value)
    }

    const scrollIntoElementView = (element) => {
        element && element.scrollIntoView({
            behavior: "smooth",
            block: "start"
        })
    }

    useEffect(() => {
        if (isIsSearchActive && isDocLoaded) {
            let element
            if (workspace.curCategory) {
                element = document.getElementById('L' + focusedIndex);
            }
            else {
                element = document.getElementById('L' + searchedIndex);
            }
            scrollIntoElementView(element)
            setIsSearchActive(false)
        }
    }, [isIsSearchActive, isDocLoaded, workspace.curCategory, focusedIndex, scrollIntoElementView, setIsSearchActive])


    const handleSearchPanelClick = (e) => {

        let id = e.target.parentNode.parentNode.id;
        const lastIndex = id.lastIndexOf('-');
        const docid = id.slice(0, lastIndex);
        const index = id.slice(lastIndex + 1);
        dispatch(setSearchedIndex(index))
        setIsSearchActive(true)
        const element = document.getElementById('L' + index);
        scrollIntoElementView(element)

        if (docid != workspace.curDocName) {
            dispatch(setIsDocLoaded(false))
            dispatch(fetchCertainDocument({ docid, id, switchStatus: 'switch' })).then(() => {
                dispatch(setFocusedState(index))
                dispatch(setIsDocLoaded(true))
            })
        } else {
            dispatch(setFocusedState(index))
        }

    }

    useEffect(() => {
        if (workspace.curCategory) {
            handleSearch()
        }
    }, [workspace.curCategory])


    return {
        handleSearchPanelClick,
        handleSearch,
        handleSearchInputChange,
        clearSearchInput,
        searchInput,
        textInput,
    }
};

export default useSearchElement;