
import { useEffect, useState } from 'react';
import { getDatasetsAPI } from './workspaceConfigSlice'
import 'react-toastify/dist/ReactToastify.css';
import { addDocuments } from './workspaceConfigSlice'
import { toast } from 'react-toastify';
import { useDispatch, useSelector } from 'react-redux'

const useLoadDoc = () => {
    const notify = (message) => toast(message);
    const { datasets } = useSelector((state) => state.workspaces)
    const dispatch = useDispatch()
    const [datasetName, setDatasetName] = useState('');
    const handleInputChange = (e) => {
        setDatasetName(e.target.value);
    };

    let options = datasets && datasets.map((item) => ({ value: item.dataset_id, title: item.dataset_id }))
    const [file, setFile] = useState('');
    const handleFileChange = (e) => {
        setFile(e.target.files[0])
    }

    const handleLoadDoc = () => {
        if (!datasetName || !file) {
            return notify("Please fill out all the required fields!")
        }
        let formData = new FormData()
        formData.append('file', file);
        formData.append('dataset_name', datasetName)
        dispatch(addDocuments(formData)).then(() => {
            setDatasetName('')       
            dispatch(getDatasetsAPI())
            notify("The new dataset has been created")
        })
    }

    return {
        handleLoadDoc,
        handleFileChange,
        handleInputChange,
        options,
        datasets,
        datasetName
    }

};

export default useLoadDoc;